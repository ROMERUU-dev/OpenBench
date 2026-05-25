"""Measured-versus-theoretical filter validation.

Computes the theoretical Bode response from SOFIA pole data (using
``scipy.signal``) and compares it against measurements acquired through any
OpenBench ``IImpedanceAnalyzer`` backend.  All arithmetic is done with NumPy
so the module is usable without hardware (simulation mode).

Example::

    import numpy as np
    from openbench.filters.design import FilterDesigner
    from openbench.filters.topologies import (
        Approximation, DesignInputs, FilterKind, FilterSpec,
    )
    from openbench.filters.validation import FilterValidator

    inputs = DesignInputs(
        kind=FilterKind.LOWPASS,
        approximation=Approximation.BUTTERWORTH,
        spec=FilterSpec(passband_hz=1_000.0, stopband_hz=5_000.0),
        passband_ripple_db=1.0,
        stopband_attenuation_db=40.0,
    )
    result = FilterDesigner(inputs).design()
    validator = FilterValidator(result, inputs)
    freqs = np.logspace(1, 5, 200)
    mag_db, phase_deg = validator.theoretical_response(freqs)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import freqs, zpk2tf

from .topologies import DesignInputs, DesignResult, FilterKind

logger = logging.getLogger(__name__)

try:
    from openbench.core.interfaces import IImpedanceAnalyzer, ImpedancePoint

    _INTERFACES_AVAILABLE = True
except Exception:
    _INTERFACES_AVAILABLE = False


@dataclass
class ValidationResult:
    """Comparison between SOFIA theoretical response and measured data.

    Attributes:
        frequencies_hz: Frequency points in hertz (shared axis).
        theoretical_magnitude_db: Theoretical magnitude response in dB.
        theoretical_phase_deg: Theoretical phase response in degrees.
        measured_magnitude_db: Measured magnitude response in dB, when
            provided.
        measured_phase_deg: Measured phase response in degrees, when provided.
        magnitude_error_db: Point-wise difference (measured − theoretical) in
            dB, when both datasets are present.
        phase_error_deg: Point-wise phase difference in degrees, when both
            datasets are present.
        rms_magnitude_error_db: RMS of ``magnitude_error_db``, when computed.
        rms_phase_error_deg: RMS of ``phase_error_deg``, when computed.
        warnings: Non-fatal issues encountered during validation.
    """

    frequencies_hz: np.ndarray
    theoretical_magnitude_db: np.ndarray
    theoretical_phase_deg: np.ndarray
    measured_magnitude_db: np.ndarray | None = None
    measured_phase_deg: np.ndarray | None = None
    magnitude_error_db: np.ndarray | None = None
    phase_error_deg: np.ndarray | None = None
    rms_magnitude_error_db: float | None = None
    rms_phase_error_deg: float | None = None
    warnings: list[str] = field(default_factory=list)


class FilterValidator:
    """Validates an active filter design against measured or simulated data.

    Args:
        result: SOFIA ``DesignResult`` returned by ``FilterDesigner.design()``.
        inputs: The ``DesignInputs`` used to produce ``result``.
    """

    def __init__(self, result: DesignResult, inputs: DesignInputs) -> None:
        self._result = result
        self._inputs = inputs

    def theoretical_response(
        self, frequencies_hz: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute the theoretical Bode response from SOFIA's pole data.

        The poles stored in ``DesignResult.poles`` are the physical (already
        frequency-scaled) analog poles in rad/s.  This method constructs the
        analog transfer function H(s) = k / ∏(s − pᵢ) with ``k`` chosen so
        that the passband reference gain is 0 dB, then evaluates |H(jω)| and
        ∠H(jω) at the requested frequencies.

        Bandpass and bandstop approximations use the lowpass-equivalent
        magnitude response; a warning is logged when the approximation is
        active.

        Args:
            frequencies_hz: 1-D array of frequencies in hertz at which to
                evaluate the response.

        Returns:
            Tuple ``(magnitude_db, phase_deg)`` — two 1-D NumPy arrays
            parallel to ``frequencies_hz``.
        """

        frequencies_rad = 2.0 * np.pi * np.asarray(frequencies_hz, dtype=float)
        poles = self._result.poles
        kind = self._inputs.kind

        if kind is FilterKind.LOWPASS:
            zeros: list[complex] = []
            k = float(np.abs(np.prod([-p for p in poles])))
        elif kind is FilterKind.HIGHPASS:
            # LP→HP transformation places a zero at s=0 for every pole.
            zeros = [0.0j] * len(poles)
            k = 1.0
        else:
            logger.warning(
                "Theoretical response for %s filters uses the LP-equivalent "
                "pole magnitude; phase accuracy is limited.",
                kind,
            )
            zeros = []
            k = float(np.abs(np.prod([-p for p in poles])))

        b, a = zpk2tf(zeros, poles, k)
        _, h = freqs(b, a, worN=frequencies_rad)

        magnitude_db = 20.0 * np.log10(np.maximum(np.abs(h), 1e-300))
        phase_deg = np.degrees(np.angle(h))
        return magnitude_db, phase_deg

    def validate_with_measurements(
        self,
        frequencies_hz: np.ndarray,
        measured_magnitude_db: np.ndarray,
        measured_phase_deg: np.ndarray | None = None,
    ) -> ValidationResult:
        """Compare the theoretical response against externally supplied data.

        Args:
            frequencies_hz: Frequency points in hertz for both datasets.
            measured_magnitude_db: Measured gain in dB, parallel to
                ``frequencies_hz``.
            measured_phase_deg: Optional measured phase in degrees, parallel
                to ``frequencies_hz``.

        Returns:
            ``ValidationResult`` with error metrics populated.
        """

        mag_theory, phase_theory = self.theoretical_response(frequencies_hz)
        freqs_arr = np.asarray(frequencies_hz, dtype=float)
        meas_mag = np.asarray(measured_magnitude_db, dtype=float)

        mag_error = meas_mag - mag_theory
        rms_mag = float(np.sqrt(np.mean(mag_error**2)))

        phase_error: np.ndarray | None = None
        rms_phase: float | None = None
        if measured_phase_deg is not None:
            meas_phase = np.asarray(measured_phase_deg, dtype=float)
            phase_error = meas_phase - phase_theory
            rms_phase = float(np.sqrt(np.mean(phase_error**2)))

        logger.info(
            "Filter validation: RMS magnitude error = %.3f dB", rms_mag
        )

        return ValidationResult(
            frequencies_hz=freqs_arr,
            theoretical_magnitude_db=mag_theory,
            theoretical_phase_deg=phase_theory,
            measured_magnitude_db=meas_mag,
            measured_phase_deg=np.asarray(measured_phase_deg) if measured_phase_deg is not None else None,
            magnitude_error_db=mag_error,
            phase_error_deg=phase_error,
            rms_magnitude_error_db=rms_mag,
            rms_phase_error_deg=rms_phase,
        )

    def validate_with_impedance(
        self,
        analyzer: "IImpedanceAnalyzer",
        frequencies_hz: np.ndarray,
        *,
        reference_ohm: float = 50.0,
        excitation_v: float | None = None,
    ) -> ValidationResult:
        """Measure the filter via an impedance analyzer and compare to theory.

        The filter under test is assumed to be connected in series with
        ``reference_ohm``; voltage division gives the transfer function gain::

            |H(f)| = Z_filter / (Z_filter + R_ref)

        In simulation mode the analyzer is never contacted and the method
        returns a theory-only ``ValidationResult`` with a warning.

        Args:
            analyzer: Connected ``IImpedanceAnalyzer`` backend instance.
            frequencies_hz: Frequency points in hertz for the sweep.
            reference_ohm: Series reference resistor value in ohms used in the
                voltage-divider model.
            excitation_v: Optional excitation voltage override in volts.

        Returns:
            ``ValidationResult`` with measured data populated when hardware is
            reachable, otherwise theory-only.
        """

        if not _INTERFACES_AVAILABLE:
            logger.warning(
                "openbench.core.interfaces not available; returning theory-only result."
            )
            mag_theory, phase_theory = self.theoretical_response(frequencies_hz)
            return ValidationResult(
                frequencies_hz=np.asarray(frequencies_hz),
                theoretical_magnitude_db=mag_theory,
                theoretical_phase_deg=phase_theory,
                warnings=["Hardware interfaces unavailable; measurement skipped."],
            )

        from openbench.core.interfaces import InstrumentStatus

        if analyzer.status() not in {
            InstrumentStatus.CONNECTED,
            InstrumentStatus.SIMULATED,
        }:
            raise RuntimeError(
                "IImpedanceAnalyzer must be connected before calling validate_with_impedance."
            )

        is_simulated = analyzer.status() is InstrumentStatus.SIMULATED
        warnings: list[str] = []

        if is_simulated:
            warnings.append(
                "Analyzer is in simulation mode; measured data mirrors theoretical response."
            )
            mag_theory, phase_theory = self.theoretical_response(frequencies_hz)
            return ValidationResult(
                frequencies_hz=np.asarray(frequencies_hz),
                theoretical_magnitude_db=mag_theory,
                theoretical_phase_deg=phase_theory,
                measured_magnitude_db=mag_theory.copy(),
                measured_phase_deg=phase_theory.copy(),
                magnitude_error_db=np.zeros_like(mag_theory),
                phase_error_deg=np.zeros_like(phase_theory),
                rms_magnitude_error_db=0.0,
                rms_phase_error_deg=0.0,
                warnings=warnings,
            )

        points: list[ImpedancePoint] = analyzer.sweep(
            start_hz=float(frequencies_hz[0]),
            stop_hz=float(frequencies_hz[-1]),
            num_points=len(frequencies_hz),
            excitation_v=excitation_v,
            log_scale=True,
        )

        measured_freqs = np.array([p.frequency_hz for p in points])
        measured_mag_db = np.array(
            [_impedance_to_gain_db(p, reference_ohm) for p in points]
        )
        measured_phase = np.array([p.phase_deg for p in points])

        return self.validate_with_measurements(
            measured_freqs, measured_mag_db, measured_phase
        )


def _impedance_to_gain_db(point: "ImpedancePoint", reference_ohm: float) -> float:
    """Convert an impedance measurement to a voltage-divider gain in dB.

    Args:
        point: Single-frequency impedance measurement.
        reference_ohm: Series reference resistor in ohms.

    Returns:
        Gain in dB: 20·log10(|Z_filter| / (|Z_filter| + R_ref)).
    """

    z = point.magnitude_ohm
    gain = z / (z + reference_ohm)
    return 20.0 * float(np.log10(max(gain, 1e-300)))


__all__ = [
    "FilterValidator",
    "ValidationResult",
]
