"""Filter-design validation experiment for OpenBench.

Designs an active filter with SOFIA, acquires a Bode sweep using an
``IImpedanceAnalyzer`` backend, and compares the measured frequency response
against the SOFIA theoretical prediction.  Runs end-to-end in simulation mode
without hardware.

Example::

    from openbench.experiments.filter_design_experiment import (
        FilterDesignExperiment,
        FilterDesignExperimentConfig,
    )
    from openbench.filters import (
        Approximation,
        DesignInputs,
        FilterKind,
        FilterSpec,
        Topology,
    )

    inputs = DesignInputs(
        kind=FilterKind.LOWPASS,
        approximation=Approximation.BUTTERWORTH,
        spec=FilterSpec(passband_hz=1_000.0, stopband_hz=5_000.0),
        passband_ripple_db=1.0,
        stopband_attenuation_db=40.0,
        topology=Topology.SALLEN_KEY,
    )
    config = FilterDesignExperimentConfig(design_inputs=inputs)
    exp = FilterDesignExperiment(
        name="butterworth-lp-1kHz",
        config=config,
        simulate=True,
    )
    result = exp.run()
    print(result.data["validation"])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from openbench.core.experiment import BaseExperiment
from openbench.core.interfaces import IImpedanceAnalyzer
from openbench.filters.design import FilterDesigner, MeasurementSetup
from openbench.filters.topologies import DesignInputs, DesignResult
from openbench.filters.validation import FilterValidator, ValidationResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterDesignExperimentConfig:
    """Configuration for a FilterDesignExperiment run.

    Attributes:
        design_inputs: SOFIA filter specification passed to ``FilterDesigner``.
        num_sweep_points: Number of logarithmically-spaced frequency points for
            the Bode sweep.
        reference_ohm: Series reference resistor value in ohms used in the
            voltage-divider impedance-to-gain conversion.
        excitation_v: Analyzer excitation amplitude in volts (peak or RMS
            depending on the backend; passed through unchanged).
        tolerance_db: Maximum acceptable RMS magnitude deviation in dB between
            theory and measurement.  The result ``passed`` flag is set when the
            measured RMS error does not exceed this threshold.
    """

    design_inputs: DesignInputs
    num_sweep_points: int = 100
    reference_ohm: float = 50.0
    excitation_v: float = 0.1
    tolerance_db: float = 3.0


@dataclass
class FilterDesignExperiment(BaseExperiment):
    """Design and hardware-validate an active filter in one workflow.

    Orchestrates SOFIA filter synthesis, an impedance sweep, and
    theory-versus-measurement comparison inside a single ``BaseExperiment``
    lifecycle.

    Lifecycle::

        validate()  — check config consistency
        setup()     — run SOFIA design; connect analyzer
        _run()      — Bode sweep + validate_with_impedance
        teardown()  — disconnect owned analyzer

    In simulation mode (``simulate=True``) the experiment creates a simulated
    SR860 backend.  The ``FilterValidator`` detects the simulated status and
    returns a theory-mirrored measurement, producing a perfect 0 dB error that
    trivially passes any tolerance threshold.

    Attributes:
        name: Human-readable experiment identifier.
        config: Filter design and sweep parameters.
        impedance_analyzer: Optional ``IImpedanceAnalyzer`` adapter.  When
            omitted, an ``SR860Backend`` is created lazily in ``setup()``.
        simulate: When ``True``, the experiment runs without hardware.
    """

    config: FilterDesignExperimentConfig
    impedance_analyzer: IImpedanceAnalyzer | None = field(default=None, repr=False)

    _owns_analyzer: bool = field(default=False, init=False, repr=False)
    _designer: FilterDesigner | None = field(default=None, init=False, repr=False)
    _design_result: DesignResult | None = field(default=None, init=False, repr=False)
    _measurement_setup: MeasurementSetup | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate experiment configuration before any hardware interaction.

        Raises:
            ValueError: When config values are out of range or inconsistent.
        """
        cfg = self.config
        if cfg.num_sweep_points < 2:
            raise ValueError("num_sweep_points must be >= 2")
        if cfg.num_sweep_points > 10_000:
            raise ValueError("num_sweep_points is too large (max 10 000)")
        if cfg.reference_ohm <= 0.0:
            raise ValueError("reference_ohm must be positive")
        if cfg.excitation_v <= 0.0:
            raise ValueError("excitation_v must be positive")
        if cfg.tolerance_db <= 0.0:
            raise ValueError("tolerance_db must be positive")

    def setup(self) -> None:
        """Design the filter and connect the impedance analyzer.

        Runs SOFIA synthesis first so that instrument connections are only
        attempted after the design is confirmed valid.  A simulated SR860
        backend is created automatically when no analyzer is injected and
        ``simulate=True``.
        """
        cfg = self.config

        self._designer = FilterDesigner(cfg.design_inputs)
        self.report_progress("Running SOFIA filter synthesis", 0.05)
        self._design_result = self._designer.design()
        self._measurement_setup = self._designer.measurement_setup(self._design_result)
        logger.info(
            "Filter design complete: order=%d, stages=%d, topology=%s",
            self._design_result.order,
            len(self._design_result.stages),
            cfg.design_inputs.topology,
        )

        # Discard an injected real analyzer when the experiment is forced into
        # simulation mode so that hardware is never contacted.
        if (
            self._simulate
            and self.impedance_analyzer is not None
            and not getattr(self.impedance_analyzer, "simulate", False)
        ):
            logger.info(
                "Discarding injected hardware analyzer for simulation mode: %s",
                self.impedance_analyzer.name,
            )
            self.impedance_analyzer = None
            self._owns_analyzer = False

        if self.impedance_analyzer is None:
            from openbench.backends.sr860_backend import SR860Backend

            self.impedance_analyzer = SR860Backend(
                name="sr860-filter-validation",
                simulate=self._simulate,
                series_resistor_ohm=cfg.reference_ohm,
                excitation_v=cfg.excitation_v,
            )
            self._owns_analyzer = True

        self.impedance_analyzer.connect()
        logger.info(
            "Filter validation analyzer connected: %s (status=%s)",
            self.impedance_analyzer.name,
            self.impedance_analyzer.status(),
        )

    def _run(self) -> dict[str, Any]:
        """Execute the Bode sweep and compare against the SOFIA prediction.

        Builds a logarithmic frequency array from the recommended sweep range
        returned by :meth:`~openbench.filters.design.FilterDesigner.measurement_setup`,
        delegates the measurement to
        :meth:`~openbench.filters.validation.FilterValidator.validate_with_impedance`,
        and packages the result into a structured dictionary.

        Returns:
            Dictionary with the following top-level keys:

            * ``filter_design`` — SOFIA synthesis summary (order, topology, …)
            * ``measurement_setup`` — sweep parameters used
            * ``frequencies_hz`` — frequency axis list
            * ``theoretical_magnitude_db`` / ``theoretical_phase_deg`` — SOFIA
              prediction on the sweep axis
            * ``measured_magnitude_db`` / ``measured_phase_deg`` — analyzer
              readings, or ``None`` when unavailable
            * ``validation`` — error metrics and pass/fail flag
            * ``simulated`` — whether the run used simulated hardware

        Raises:
            RuntimeError: When ``setup()`` was not called or the analyzer is
                missing.
        """
        if self._design_result is None or self._measurement_setup is None:
            raise RuntimeError("setup() must complete before _run()")
        if self.impedance_analyzer is None:
            raise RuntimeError("An impedance analyzer is required")

        cfg = self.config
        setup = self._measurement_setup

        frequencies_hz = np.logspace(
            np.log10(setup.start_hz),
            np.log10(setup.stop_hz),
            cfg.num_sweep_points,
        )

        validator = FilterValidator(self._design_result, cfg.design_inputs)

        self.report_progress("Starting Bode frequency sweep", 0.2)
        if self._abort_requested:
            raise RuntimeError("aborted")

        vr: ValidationResult = validator.validate_with_impedance(
            self.impedance_analyzer,
            frequencies_hz,
            reference_ohm=cfg.reference_ohm,
            excitation_v=cfg.excitation_v,
        )

        self.report_progress("Computing error metrics", 0.9)

        rms_error = vr.rms_magnitude_error_db
        passed = rms_error is not None and rms_error <= cfg.tolerance_db

        if rms_error is not None:
            logger.info(
                "Filter validation: RMS error=%.3f dB  tolerance=%.1f dB  → %s",
                rms_error,
                cfg.tolerance_db,
                "PASS" if passed else "FAIL",
            )
        else:
            logger.info("Filter validation: theory-only (no measured data)")

        self.report_progress("Filter design validation complete", 1.0)

        return {
            "filter_design": {
                "order": self._design_result.order,
                "stages": len(self._design_result.stages),
                "topology": str(cfg.design_inputs.topology),
                "approximation": str(cfg.design_inputs.approximation),
                "kind": str(cfg.design_inputs.kind),
                "passband_hz": cfg.design_inputs.spec.passband_hz,
                "warnings": list(self._design_result.warnings),
            },
            "measurement_setup": {
                "start_hz": setup.start_hz,
                "stop_hz": setup.stop_hz,
                "num_points": cfg.num_sweep_points,
                "excitation_v": cfg.excitation_v,
                "notes": list(setup.notes),
            },
            "frequencies_hz": vr.frequencies_hz.tolist(),
            "theoretical_magnitude_db": vr.theoretical_magnitude_db.tolist(),
            "theoretical_phase_deg": vr.theoretical_phase_deg.tolist(),
            "measured_magnitude_db": (
                vr.measured_magnitude_db.tolist()
                if vr.measured_magnitude_db is not None
                else None
            ),
            "measured_phase_deg": (
                vr.measured_phase_deg.tolist()
                if vr.measured_phase_deg is not None
                else None
            ),
            "validation": {
                "rms_magnitude_error_db": rms_error,
                "rms_phase_error_deg": vr.rms_phase_error_deg,
                "tolerance_db": cfg.tolerance_db,
                "passed": passed,
                "warnings": list(vr.warnings),
            },
            "simulated": self._simulate,
        }

    def teardown(self) -> None:
        """Disconnect an impedance analyzer that was created by this experiment."""
        if self.impedance_analyzer is not None and self._owns_analyzer:
            try:
                self.impedance_analyzer.disconnect()
            except Exception:
                logger.warning(
                    "Could not disconnect owned analyzer %s",
                    self.impedance_analyzer.name,
                    exc_info=True,
                )


__all__ = [
    "FilterDesignExperiment",
    "FilterDesignExperimentConfig",
]
