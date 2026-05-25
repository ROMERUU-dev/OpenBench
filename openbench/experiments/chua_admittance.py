"""Chua circuit admittance sweep experiment — SR860 + Keysight DC bias."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from openbench.core.experiment import BaseExperiment
from openbench.core.interfaces import (
    IDCSupply,
    IImpedanceAnalyzer,
    ImpedancePoint,
    InstrumentChannel,
)

logger = logging.getLogger(__name__)

_SR860_MIN_FREQ_HZ = 1e-3
_SR860_MAX_FREQ_HZ = 500_000.0


@dataclass(frozen=True)
class ChuaAdmittanceSweepConfig:
    """Configuration for the Chua circuit admittance vs. DC bias sweep.

    The experiment applies a series of DC bias voltages to the Chua nonlinear
    element (Chua diode) via the Keysight E36312A supply, while the SR860
    lock-in measures the small-signal AC impedance at each bias point.
    Admittance Y = 1/Z is derived from each measurement, tracing the N-shaped
    negative-resistance characteristic of the Chua diode.

    Measurement circuit topology::

        Keysight CH_bias → Chua element → GND
                                ↑
                    SR860 SINE OUT → R_series → Chua element → GND
                                        ↑
                                SR860 input A

    The SR860 AC excitation amplitude must be small relative to the DC bias
    step so that the measurement stays in the small-signal linear regime at
    each operating point.

    Attributes:
        bias_start_v: First DC bias voltage setpoint in volts.
        bias_stop_v: Last DC bias voltage setpoint in volts.
        bias_step_v: DC bias step magnitude in volts. Must be positive; sweep
            direction is inferred automatically from start and stop.
        bias_current_limit_a: Current compliance for the Keysight bias channel
            in amperes. Protects the Chua element from overcurrent.
        bias_channel: Keysight supply channel identifier wired to the Chua
            element DC bias input.
        ac_frequency_hz: SR860 internal oscillator frequency for the
            small-signal impedance measurement in hertz.
        excitation_v: SR860 SINE OUT amplitude in volts RMS. Should be small
            (typ. 10–100 mV) to stay in the small-signal regime.
        settle_s: Time in seconds to wait after each bias step before the
            SR860 snapshot is taken.
        settle_periods: Number of SR860 time-constant periods to wait before
            reading the lock-in X/Y snapshot (hardware mode only).
        time_constant_s: SR860 lock-in filter time constant in seconds.
        series_resistor_ohm: Reference series resistor value in the SR860
            series-divider measurement circuit in ohms.
        source_series_ohm: SR860 SINE OUT output impedance in ohms.
        simulation_ga_s: Chua diode inner-segment small-signal conductance Ga
            in siemens. Negative value represents the inner negative-resistance
            region (default −0.757 mS, classic Chua parameters).
        simulation_gb_s: Chua diode outer-segment small-signal conductance Gb
            in siemens. Negative value represents outer negative resistance
            (default −0.409 mS, classic Chua parameters).
        simulation_bp_v: Chua diode breakpoint voltage Bp in volts. Bias
            voltages |V| < Bp fall in the inner segment; |V| >= Bp in outer.
        simulation_cpar_f: Parasitic parallel capacitance of the simulated
            Chua element in farads. Adds a positive susceptance component to
            the simulated admittance.
    """

    bias_start_v: float = -2.0
    bias_stop_v: float = 2.0
    bias_step_v: float = 0.1
    bias_current_limit_a: float = 0.05
    bias_channel: InstrumentChannel = "CH1"
    ac_frequency_hz: float = 1_000.0
    excitation_v: float = 0.1
    settle_s: float = 0.05
    settle_periods: int = 5
    time_constant_s: float = 0.1
    series_resistor_ohm: float = 220.0
    source_series_ohm: float = 50.0
    simulation_ga_s: float = -0.757e-3
    simulation_gb_s: float = -0.409e-3
    simulation_bp_v: float = 1.0
    simulation_cpar_f: float = 10e-9


@dataclass(frozen=True)
class ChuaAdmittancePoint:
    """Admittance measurement at one DC bias operating point.

    Admittance quantities are derived from the SR860 impedance measurement
    using Y = 1/Z, G = Re(Y), B = Im(Y).

    Attributes:
        bias_voltage_v: Programmed DC bias voltage setpoint in volts.
        measured_bias_v: Measured DC voltage at the bias channel in volts, or
            ``None`` when the supply does not support voltage readback.
        measured_bias_current_a: Measured DC current at the bias channel in
            amperes, or ``None`` when current readback is unavailable.
        frequency_hz: SR860 AC reference frequency in hertz.
        z_real_ohm: Real part of the small-signal impedance in ohms.
        z_imag_ohm: Imaginary part of the small-signal impedance in ohms.
        magnitude_ohm: Impedance magnitude |Z| in ohms.
        phase_deg: Impedance phase angle in degrees.
        admittance_s: Admittance magnitude |Y| = 1/|Z| in siemens.
        conductance_s: Real part of admittance G = R/|Z|² in siemens.
            Negative values indicate a negative-resistance operating region.
        susceptance_s: Imaginary part of admittance B = −X/|Z|² in siemens.
        metadata: Backend-specific raw details preserved from the SR860
            measurement (X/Y voltages, backend tag, etc.).
    """

    bias_voltage_v: float
    measured_bias_v: float | None
    measured_bias_current_a: float | None
    frequency_hz: float
    z_real_ohm: float
    z_imag_ohm: float
    magnitude_ohm: float
    phase_deg: float
    admittance_s: float
    conductance_s: float
    susceptance_s: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChuaAdmittanceSweep(BaseExperiment):
    """Sweep the Chua nonlinear element admittance as a function of DC bias.

    Composes two OpenBench backends:
    - ``IDCSupply`` (Keysight E36312A) to apply DC bias setpoints.
    - ``IImpedanceAnalyzer`` (SR860) to measure small-signal AC impedance.

    When no instrument adapter is injected, the experiment creates the
    appropriate backend lazily during ``setup()``, preserving standalone
    backend compatibility. In simulation mode, the Chua diode piecewise-linear
    model generates realistic admittance-vs-bias data without hardware access.

    Attributes:
        name: Human-readable experiment identifier.
        config: Sweep, measurement, and simulation configuration.
        dc_supply: Optional DC supply adapter. If ``None`` in hardware mode,
            a ``KeysightE36312ABackend`` is created lazily.
        impedance_analyzer: Optional impedance analyzer adapter. If ``None``,
            an ``SR860Backend`` is created lazily.
        simulate: When ``True``, run the full experiment without hardware.
    """

    config: ChuaAdmittanceSweepConfig = field(
        default_factory=ChuaAdmittanceSweepConfig
    )
    dc_supply: IDCSupply | None = field(default=None, repr=False)
    impedance_analyzer: IImpedanceAnalyzer | None = field(default=None, repr=False)

    _owns_supply: bool = field(default=False, init=False, repr=False)
    _owns_analyzer: bool = field(default=False, init=False, repr=False)

    def validate(self) -> None:
        """Validate sweep, safety, and measurement configuration.

        Raises:
            ValueError: If any configuration parameter is outside safe or
                physically meaningful bounds.
        """
        cfg = self.config
        if cfg.bias_step_v <= 0.0:
            raise ValueError("bias_step_v must be positive")
        if cfg.bias_current_limit_a <= 0.0:
            raise ValueError("bias_current_limit_a must be positive")
        if cfg.ac_frequency_hz < _SR860_MIN_FREQ_HZ or cfg.ac_frequency_hz > _SR860_MAX_FREQ_HZ:
            raise ValueError(
                f"ac_frequency_hz must be in [{_SR860_MIN_FREQ_HZ:g}, {_SR860_MAX_FREQ_HZ:g}] Hz"
            )
        if cfg.excitation_v <= 0.0:
            raise ValueError("excitation_v must be positive")
        if cfg.settle_s < 0.0:
            raise ValueError("settle_s must be >= 0")
        if cfg.settle_periods < 0:
            raise ValueError("settle_periods must be >= 0")
        if cfg.time_constant_s <= 0.0:
            raise ValueError("time_constant_s must be positive")
        if cfg.series_resistor_ohm <= 0.0:
            raise ValueError("series_resistor_ohm must be positive")
        if cfg.source_series_ohm < 0.0:
            raise ValueError("source_series_ohm must be >= 0")
        if cfg.simulation_bp_v <= 0.0:
            raise ValueError("simulation_bp_v must be positive")
        if cfg.simulation_cpar_f < 0.0:
            raise ValueError("simulation_cpar_f must be >= 0")
        if len(self._bias_values()) < 1:
            raise ValueError(
                "Sweep configuration produces zero bias points. "
                "Check bias_start_v, bias_stop_v, and bias_step_v."
            )

    def setup(self) -> None:
        """Connect and configure the DC supply and SR860 impedance analyzer.

        In simulation mode, instruments are not created; synthetic data is
        generated entirely in ``_run()``. In hardware mode, missing instruments
        are created from the appropriate backend defaults.
        """
        if self._simulate:
            logger.info("ChuaAdmittanceSweep using simulation mode — no hardware access")
            return

        cfg = self.config

        if self.dc_supply is None:
            from openbench.backends.keysight_backend import KeysightE36312ABackend

            self.dc_supply = KeysightE36312ABackend(
                name="keysight-chua-bias",
                simulate=False,
            )
            self._owns_supply = True

        if self.impedance_analyzer is None:
            from openbench.backends.sr860_backend import SR860Backend

            self.impedance_analyzer = SR860Backend(
                name="sr860-chua-admittance",
                simulate=False,
                series_resistor_ohm=cfg.series_resistor_ohm,
                source_series_ohm=cfg.source_series_ohm,
                excitation_v=cfg.excitation_v,
                time_constant_s=cfg.time_constant_s,
            )
            self._owns_analyzer = True

        self.dc_supply.connect()
        self.impedance_analyzer.connect()

        self._configure_supply()
        self._configure_analyzer()
        logger.info("ChuaAdmittanceSweep instruments configured")

    def _run(self) -> dict[str, Any]:
        """Execute the admittance vs. bias sweep.

        Returns:
            Dictionary with per-point admittance data, a statistical summary,
            sweep metadata, and the simulation flag.
        """
        bias_values = self._bias_values()

        if self._simulate:
            points = self._simulate_points(bias_values)
        else:
            points = self._measure_points(bias_values)

        summary = self._summarize(points)

        cfg = self.config
        return {
            "experiment": "chua_admittance_sweep",
            "points": [self._point_to_dict(p) for p in points],
            "summary": summary,
            "point_count": len(points),
            "simulated": self._simulate,
            "sweep": {
                "bias_start_v": cfg.bias_start_v,
                "bias_stop_v": cfg.bias_stop_v,
                "bias_step_v": cfg.bias_step_v,
                "ac_frequency_hz": cfg.ac_frequency_hz,
                "excitation_v": cfg.excitation_v,
                "settle_s": cfg.settle_s,
            },
        }

    def teardown(self) -> None:
        """Return the bias output to 0 V and release owned instruments."""
        cfg = self.config
        if self.dc_supply is not None and not self._simulate:
            try:
                self.dc_supply.set_voltage(cfg.bias_channel, 0.0)
                enable = getattr(self.dc_supply, "enable_output", None)
                if callable(enable):
                    enable(cfg.bias_channel, enabled=False)
            except Exception:
                logger.warning("Could not reset bias channel to 0 V", exc_info=True)

        for instrument, owned in (
            (self.impedance_analyzer, self._owns_analyzer),
            (self.dc_supply, self._owns_supply),
        ):
            if instrument is not None and owned:
                try:
                    instrument.disconnect()
                except Exception:
                    logger.warning(
                        "Could not disconnect owned instrument %s",
                        instrument.name,
                        exc_info=True,
                    )

    # ------------------------------------------------------------------
    # Hardware measurement path
    # ------------------------------------------------------------------

    def _measure_points(self, bias_values: list[float]) -> list[ChuaAdmittancePoint]:
        supply = self.dc_supply
        analyzer = self.impedance_analyzer
        if supply is None or analyzer is None:
            raise RuntimeError("DC supply and impedance analyzer are required for hardware mode")

        cfg = self.config
        total = max(1, len(bias_values))
        settle_periods = cfg.settle_periods
        points: list[ChuaAdmittancePoint] = []

        for index, bias_v in enumerate(bias_values):
            if self._abort_requested:
                raise RuntimeError("aborted")

            self.report_progress(f"Bias={bias_v:.3f} V", index / total)
            supply.set_voltage(cfg.bias_channel, bias_v)
            if cfg.settle_s > 0.0:
                time.sleep(cfg.settle_s)

            impedance_point = analyzer.measure_at_freq(
                cfg.ac_frequency_hz,
                excitation_v=cfg.excitation_v,
                settle_periods=settle_periods,
            )

            meas_v = self._read_supply_voltage(supply, cfg.bias_channel)
            meas_i = self._read_supply_current(supply, cfg.bias_channel)

            points.append(
                self._point_from_impedance(bias_v, meas_v, meas_i, impedance_point)
            )

        self.report_progress("ChuaAdmittanceSweep complete", 1.0)
        return points

    # ------------------------------------------------------------------
    # Simulation path — Chua diode piecewise-linear model
    # ------------------------------------------------------------------

    def _simulate_points(self, bias_values: list[float]) -> list[ChuaAdmittancePoint]:
        cfg = self.config
        total = max(1, len(bias_values))
        points: list[ChuaAdmittancePoint] = []

        for index, bias_v in enumerate(bias_values):
            if self._abort_requested:
                raise RuntimeError("aborted")

            self.report_progress(f"Bias={bias_v:.3f} V (sim)", index / total)
            impedance_point = self._chua_model_impedance(bias_v, cfg.ac_frequency_hz)
            points.append(
                self._point_from_impedance(
                    bias_v,
                    measured_bias_v=bias_v,
                    measured_bias_current_a=self._chua_dc_current(bias_v),
                    impedance_point=impedance_point,
                )
            )

        self.report_progress("ChuaAdmittanceSweep simulation complete", 1.0)
        return points

    def _chua_model_impedance(self, bias_v: float, frequency_hz: float) -> ImpedancePoint:
        """Compute impedance from the Chua diode piecewise-linear model.

        The Chua element is modelled as a conductance G(V) in parallel with a
        parasitic capacitance Cpar:
            Y(V, f) = G_chua(V) + j·2πf·Cpar
            Z = 1 / Y

        Args:
            bias_v: DC bias voltage setpoint in volts.
            frequency_hz: AC measurement frequency in hertz.

        Returns:
            Simulated ``ImpedancePoint`` at the given bias and frequency.
        """
        cfg = self.config
        g_chua = cfg.simulation_ga_s if abs(bias_v) < cfg.simulation_bp_v else cfg.simulation_gb_s

        omega = 2.0 * math.pi * frequency_hz
        y_real = g_chua
        y_imag = omega * cfg.simulation_cpar_f

        y_mag_sq = y_real ** 2 + y_imag ** 2
        if y_mag_sq < 1e-30:
            z_real, z_imag = 1e9, 0.0
        else:
            z_real = y_real / y_mag_sq
            z_imag = -y_imag / y_mag_sq

        # Repeatable per-point noise derived from the bias hash
        seed_bits = int(bias_v * 1000) & 0x7FFF_FFFF
        noise_frac = ((seed_bits * 6364136223846793005 + 1442695040888963407) & 0xFFFF) / 0xFFFF - 0.5
        noise_scale = abs(z_real) * 2e-3
        z_real += noise_scale * noise_frac

        z_mag = math.sqrt(z_real ** 2 + z_imag ** 2)
        z_phase = math.degrees(math.atan2(z_imag, z_real))

        return ImpedancePoint(
            frequency_hz=frequency_hz,
            z_real_ohm=z_real,
            z_imag_ohm=z_imag,
            phase_deg=z_phase,
            magnitude_ohm=z_mag,
            metadata={"simulated": True, "bias_v": bias_v, "g_chua_s": g_chua},
        )

    def _chua_dc_current(self, bias_v: float) -> float:
        """Approximate DC operating current from the Chua diode i-V model.

        Uses the integrated piecewise-linear i-V characteristic:
            i(V) = Gb·V + 0.5·(Ga−Gb)·(|V+Bp| − |V−Bp|)

        Args:
            bias_v: DC bias voltage in volts.

        Returns:
            Approximate DC current in amperes.
        """
        cfg = self.config
        ga, gb, bp = cfg.simulation_ga_s, cfg.simulation_gb_s, cfg.simulation_bp_v
        return gb * bias_v + 0.5 * (ga - gb) * (abs(bias_v + bp) - abs(bias_v - bp))

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _point_from_impedance(
        self,
        bias_voltage_v: float,
        measured_bias_v: float | None,
        measured_bias_current_a: float | None,
        impedance_point: ImpedancePoint,
    ) -> ChuaAdmittancePoint:
        z_r = impedance_point.z_real_ohm
        z_x = impedance_point.z_imag_ohm
        z_mag = impedance_point.magnitude_ohm

        if z_mag > 1e-30:
            admittance_s = 1.0 / z_mag
            z_mag_sq = z_mag ** 2
            conductance_s = z_r / z_mag_sq
            susceptance_s = -z_x / z_mag_sq
        else:
            admittance_s = 0.0
            conductance_s = 0.0
            susceptance_s = 0.0

        return ChuaAdmittancePoint(
            bias_voltage_v=bias_voltage_v,
            measured_bias_v=measured_bias_v,
            measured_bias_current_a=measured_bias_current_a,
            frequency_hz=impedance_point.frequency_hz,
            z_real_ohm=z_r,
            z_imag_ohm=z_x,
            magnitude_ohm=z_mag,
            phase_deg=impedance_point.phase_deg,
            admittance_s=admittance_s,
            conductance_s=conductance_s,
            susceptance_s=susceptance_s,
            metadata=dict(impedance_point.metadata),
        )

    def _bias_values(self) -> list[float]:
        cfg = self.config
        step = abs(cfg.bias_step_v)
        direction = 1.0 if cfg.bias_stop_v >= cfg.bias_start_v else -1.0
        signed_step = direction * step
        epsilon = step * 1e-9
        values: list[float] = []
        v = cfg.bias_start_v
        while direction * (v - cfg.bias_stop_v) <= epsilon:
            values.append(round(v, 12))
            v += signed_step
            if len(values) > 10_000:
                raise ValueError("Chua admittance sweep would generate too many bias points")
        if values and abs(values[-1] - cfg.bias_stop_v) > epsilon:
            values.append(round(cfg.bias_stop_v, 12))
        return values

    def _summarize(self, points: list[ChuaAdmittancePoint]) -> dict[str, Any]:
        if not points:
            return {"point_count": 0}

        conductances = [p.conductance_s for p in points]
        admittances = [p.admittance_s for p in points]

        g_min = min(conductances)
        g_max = max(conductances)
        neg_resistance_count = sum(1 for g in conductances if g < 0.0)

        bias_at_g_min = points[conductances.index(g_min)].bias_voltage_v

        breakpoint_detected = any(
            left.conductance_s * right.conductance_s < 0.0
            for left, right in zip(points, points[1:])
        )

        return {
            "conductance_s_min": g_min,
            "conductance_s_max": g_max,
            "admittance_s_mean": sum(admittances) / len(admittances),
            "negative_resistance_point_count": neg_resistance_count,
            "bias_v_at_min_conductance": bias_at_g_min,
            "breakpoint_detected": breakpoint_detected,
        }

    @staticmethod
    def _point_to_dict(point: ChuaAdmittancePoint) -> dict[str, Any]:
        return {
            "bias_voltage_v": point.bias_voltage_v,
            "measured_bias_v": point.measured_bias_v,
            "measured_bias_current_a": point.measured_bias_current_a,
            "frequency_hz": point.frequency_hz,
            "z_real_ohm": point.z_real_ohm,
            "z_imag_ohm": point.z_imag_ohm,
            "magnitude_ohm": point.magnitude_ohm,
            "phase_deg": point.phase_deg,
            "admittance_s": point.admittance_s,
            "conductance_s": point.conductance_s,
            "susceptance_s": point.susceptance_s,
            "metadata": point.metadata,
        }

    # ------------------------------------------------------------------
    # Instrument helpers
    # ------------------------------------------------------------------

    def _configure_supply(self) -> None:
        cfg = self.config
        if self.dc_supply is None:
            return
        self.dc_supply.set_current(cfg.bias_channel, cfg.bias_current_limit_a)
        self.dc_supply.set_voltage(cfg.bias_channel, 0.0)
        enable = getattr(self.dc_supply, "enable_output", None)
        if callable(enable):
            enable(cfg.bias_channel, enabled=True)

    def _configure_analyzer(self) -> None:
        cfg = self.config
        analyzer = self.impedance_analyzer
        if analyzer is None:
            return
        set_tc = getattr(analyzer, "set_time_constant", None)
        if callable(set_tc):
            set_tc(cfg.time_constant_s)
        set_exc = getattr(analyzer, "set_excitation", None)
        if callable(set_exc):
            set_exc(cfg.excitation_v)

    def _read_supply_voltage(
        self, supply: IDCSupply, channel: InstrumentChannel
    ) -> float | None:
        measure = getattr(supply, "measure_voltage", None)
        if not callable(measure):
            return None
        try:
            return float(measure(channel))
        except Exception:
            logger.debug("Voltage readback unavailable on %r", channel, exc_info=True)
            return None

    def _read_supply_current(
        self, supply: IDCSupply, channel: InstrumentChannel
    ) -> float | None:
        measure = getattr(supply, "measure_current", None)
        if not callable(measure):
            return None
        try:
            return float(measure(channel))
        except Exception:
            logger.debug("Current readback unavailable on %r", channel, exc_info=True)
            return None


__all__ = [
    "ChuaAdmittanceSweep",
    "ChuaAdmittanceSweepConfig",
    "ChuaAdmittancePoint",
]
