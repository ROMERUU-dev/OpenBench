"""Reusable component characterization experiments."""

from __future__ import annotations

import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from openbench.core.experiment import BaseExperiment
from openbench.core.interfaces import (
    IDCSupply,
    IImpedanceAnalyzer,
    IOscilloscope,
    ImpedancePoint,
    InstrumentChannel,
)

logger = logging.getLogger(__name__)

_SR860_MIN_SWEEP_HZ = 1e-3
_SR860_MAX_SWEEP_HZ = 500_000.0


@dataclass(frozen=True)
class TC4069UBPCharacterizationConfig:
    """Configuration for TC4069UBP inverter transfer characterization.

    The default wiring matches the Monday lab workflow: a Keysight supply drives
    VDD and the DC input bias, while a VirtualBench oscilloscope measures the
    inverter output.

    Attributes:
        supply_voltage_v: TC4069UBP VDD voltage in volts.
        input_start_v: First DC input voltage setpoint.
        input_stop_v: Last DC input voltage setpoint.
        input_step_v: DC input voltage increment.
        current_limit_a: Supply current compliance used for VDD and input bias.
        vdd_channel: DC supply channel wired to TC4069UBP VDD.
        input_channel: DC supply channel wired to inverter input bias.
        scope_output_channel: Oscilloscope channel connected to inverter output.
        dwell_s: Settling time after each input setpoint.
        output_threshold_ratio: Ratio of VDD used to estimate switching point.
        scope_volts_per_div: Vertical scope scale for the output channel.
        scope_time_per_div_s: Horizontal scope scale.
    """

    supply_voltage_v: float = 5.0
    input_start_v: float = 0.0
    input_stop_v: float = 5.0
    input_step_v: float = 0.1
    current_limit_a: float = 0.02
    vdd_channel: InstrumentChannel = "CH1"
    input_channel: InstrumentChannel = "CH2"
    scope_output_channel: InstrumentChannel = 1
    dwell_s: float = 0.02
    output_threshold_ratio: float = 0.5
    scope_volts_per_div: float = 1.0
    scope_time_per_div_s: float = 1e-3


@dataclass(frozen=True)
class TC4069UBPPoint:
    """Single measured transfer point for a TC4069UBP inverter.

    Attributes:
        input_voltage_v: Applied inverter input voltage.
        output_voltage_v: Mean measured or simulated inverter output voltage.
        output_min_v: Minimum output voltage in the acquired waveform.
        output_max_v: Maximum output voltage in the acquired waveform.
        output_rms_v: RMS output voltage in the acquired waveform.
        supply_current_a: Measured VDD current when available.
        input_current_a: Measured input-bias channel current when available.
    """

    input_voltage_v: float
    output_voltage_v: float
    output_min_v: float
    output_max_v: float
    output_rms_v: float
    supply_current_a: float | None = None
    input_current_a: float | None = None


@dataclass(frozen=True)
class InductorCharacterizationConfig:
    """Configuration for SR860-based inductor impedance characterization.

    The default sweep targets the lab Chua inductor workflow: a wide log sweep
    with SR860 sine excitation and a series-resistor divider impedance model.

    Attributes:
        start_hz: First sweep frequency in hertz.
        stop_hz: Last sweep frequency in hertz.
        num_points: Number of sweep points including endpoints.
        excitation_v: SR860 SINE OUT amplitude in volts RMS.
        log_scale: Use logarithmic spacing when True, linear spacing when False.
        settle_periods: Lock-in settle periods per frequency point in hardware
            mode. Simulation always runs with zero settle periods.
        time_constant_s: Requested SR860 lock-in time constant in seconds.
        series_resistor_ohm: External reference resistor in series with the DUT.
        source_series_ohm: Source output impedance included in the divider model.
        nominal_inductance_h: Optional expected inductance used for percent-error
            reporting in the summary.
        simulation_series_resistance_ohm: Series resistance used by the simulated
            DUT when OpenBench owns the simulated SR860 backend.
        simulation_inductance_h: Inductance used by the simulated DUT when
            OpenBench owns the simulated SR860 backend.
    """

    start_hz: float = 100.0
    stop_hz: float = 100_000.0
    num_points: int = 30
    excitation_v: float = 1.0
    log_scale: bool = True
    settle_periods: int = 5
    time_constant_s: float = 0.1
    series_resistor_ohm: float = 220.0
    source_series_ohm: float = 50.0
    nominal_inductance_h: float | None = None
    simulation_series_resistance_ohm: float = 10.0
    simulation_inductance_h: float = 44.4e-3


@dataclass(frozen=True)
class InductorCharacterizationPoint:
    """Derived inductor quantities at one impedance sweep frequency.

    Attributes:
        frequency_hz: Stimulus frequency in hertz.
        z_real_ohm: Real impedance component in ohms.
        z_imag_ohm: Imaginary impedance component in ohms.
        magnitude_ohm: Impedance magnitude in ohms.
        phase_deg: Impedance phase angle in degrees.
        equivalent_series_resistance_ohm: Apparent ESR from the real component.
        inductance_h: Apparent inductance from ``X_L / (2*pi*f)``. Negative values
            indicate capacitive behavior above self-resonance.
        quality_factor: Apparent ``X_L / ESR`` when ESR is positive.
        is_inductive: True when the imaginary component is positive.
        metadata: Backend-specific raw details preserved from the measurement.
    """

    frequency_hz: float
    z_real_ohm: float
    z_imag_ohm: float
    magnitude_ohm: float
    phase_deg: float
    equivalent_series_resistance_ohm: float
    inductance_h: float
    quality_factor: float | None
    is_inductive: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TC4069UBPCharacterization(BaseExperiment):
    """Characterize a TC4069UBP CMOS inverter transfer curve.

    The experiment biases VDD, sweeps the inverter input voltage, samples the
    output with an oscilloscope, and returns structured transfer data plus a
    switching-threshold estimate. In simulation mode the same payload is
    generated from a smooth CMOS inverter model without touching hardware.

    Attributes:
        name: Human-readable experiment identifier.
        config: Sweep, safety, and channel configuration.
        dc_supply: Optional DC supply adapter. If omitted in hardware mode, a
            ``KeysightE36312ABackend`` is created lazily.
        oscilloscope: Optional oscilloscope adapter. If omitted in hardware
            mode, a ``VirtualBenchOscilloscopeBackend`` is created lazily.
        simulate: When ``True``, use synthetic data without hardware access.
    """

    config: TC4069UBPCharacterizationConfig = field(
        default_factory=TC4069UBPCharacterizationConfig
    )
    dc_supply: IDCSupply | None = field(default=None, repr=False)
    oscilloscope: IOscilloscope | None = field(default=None, repr=False)

    _owns_supply: bool = field(default=False, init=False, repr=False)
    _owns_scope: bool = field(default=False, init=False, repr=False)

    def validate(self) -> None:
        """Validate sweep and safety configuration before instrument setup.

        Raises:
            ValueError: If the configured sweep or safety limits are invalid.
        """
        cfg = self.config
        if cfg.supply_voltage_v <= 0.0:
            raise ValueError("supply_voltage_v must be positive")
        if cfg.current_limit_a <= 0.0:
            raise ValueError("current_limit_a must be positive")
        if cfg.input_step_v == 0.0:
            raise ValueError("input_step_v must be non-zero")
        if cfg.input_stop_v > cfg.input_start_v and cfg.input_step_v < 0.0:
            raise ValueError("input_step_v must be positive for an ascending sweep")
        if cfg.input_stop_v < cfg.input_start_v and cfg.input_step_v > 0.0:
            raise ValueError("input_step_v must be negative for a descending sweep")
        if not 0.0 < cfg.output_threshold_ratio < 1.0:
            raise ValueError("output_threshold_ratio must be between 0 and 1")

    def setup(self) -> None:
        """Connect and configure the DC supply and oscilloscope."""
        if self._simulate:
            logger.info("TC4069UBP characterization using simulation mode")
            return

        cfg = self.config
        if self.dc_supply is None:
            from openbench.backends.keysight_backend import KeysightE36312ABackend

            self.dc_supply = KeysightE36312ABackend(
                name="keysight-e36312a",
                simulate=False,
            )
            self._owns_supply = True

        if self.oscilloscope is None:
            from openbench.backends.virtualbench_backend import (
                VirtualBenchOscilloscopeBackend,
            )

            self.oscilloscope = VirtualBenchOscilloscopeBackend(
                name="virtualbench-scope",
                simulate=False,
            )
            self._owns_scope = True

        self.dc_supply.connect()
        self.oscilloscope.connect()

        self.dc_supply.set_current(cfg.vdd_channel, cfg.current_limit_a)
        self.dc_supply.set_current(cfg.input_channel, cfg.current_limit_a)
        self.dc_supply.set_voltage(cfg.input_channel, 0.0)
        self.dc_supply.set_voltage(cfg.vdd_channel, cfg.supply_voltage_v)
        self._enable_supply_output(cfg.input_channel, True)
        self._enable_supply_output(cfg.vdd_channel, True)

        self.oscilloscope.configure_channel(
            cfg.scope_output_channel,
            volts_per_div=cfg.scope_volts_per_div,
            coupling="DC",
            enabled=True,
        )
        self.oscilloscope.configure_timebase(
            cfg.scope_time_per_div_s,
            trigger_level_v=cfg.supply_voltage_v * cfg.output_threshold_ratio,
            trigger_channel=cfg.scope_output_channel,
        )
        logger.info("TC4069UBP instruments configured")

    def _run(self) -> dict[str, Any]:
        """Execute the TC4069UBP input sweep and collect transfer data.

        Returns:
            Dictionary containing point data, threshold estimate, and metadata.
        """
        points = (
            self._simulate_points()
            if self._simulate
            else self._measure_points()
        )
        threshold = self._estimate_threshold(points)
        return {
            "component": "TC4069UBP",
            "supply_voltage_v": self.config.supply_voltage_v,
            "points": [point.__dict__ for point in points],
            "switching_threshold_v": threshold,
            "point_count": len(points),
            "simulated": self._simulate,
        }

    def teardown(self) -> None:
        """Return outputs to a safe state and release owned instruments."""
        cfg = self.config
        if self.dc_supply is not None and not self._simulate:
            for channel in (cfg.input_channel, cfg.vdd_channel):
                try:
                    self.dc_supply.set_voltage(channel, 0.0)
                    self._enable_supply_output(channel, False)
                except Exception:
                    logger.warning("Could not safe channel %r", channel, exc_info=True)

        for instrument, owned in (
            (self.oscilloscope, self._owns_scope),
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

    def _measure_points(self) -> list[TC4069UBPPoint]:
        supply = self.dc_supply
        scope = self.oscilloscope
        if supply is None or scope is None:
            raise RuntimeError("DC supply and oscilloscope are required")

        values = self._sweep_values()
        points: list[TC4069UBPPoint] = []
        total = max(1, len(values))

        for index, vin in enumerate(values):
            if self._abort_requested:
                raise RuntimeError("aborted")
            self.report_progress(f"VIN={vin:.3f} V", index / total)
            supply.set_voltage(self.config.input_channel, vin)
            if self.config.dwell_s:
                time.sleep(self.config.dwell_s)
            reading = scope.acquire(self.config.scope_output_channel)
            point = self._point_from_waveform(
                input_voltage_v=vin,
                waveform_v=reading.voltage_v,
                supply_current_a=self._measure_current(supply, self.config.vdd_channel),
                input_current_a=self._measure_current(supply, self.config.input_channel),
            )
            points.append(point)

        self.report_progress("TC4069UBP sweep complete", 1.0)
        return points

    def _simulate_points(self) -> list[TC4069UBPPoint]:
        values = self._sweep_values()
        points: list[TC4069UBPPoint] = []
        total = max(1, len(values))
        vdd = self.config.supply_voltage_v
        center = vdd * 0.48
        slope = max(vdd / 35.0, 0.02)

        for index, vin in enumerate(values):
            if self._abort_requested:
                raise RuntimeError("aborted")
            self.report_progress(f"VIN={vin:.3f} V (sim)", index / total)
            ideal = vdd / (1.0 + math.exp((vin - center) / slope))
            ripple = 0.01 * vdd * math.sin(2.0 * math.pi * index / max(total, 2))
            vout = max(0.0, min(vdd, ideal + ripple))
            supply_current = 0.4e-3 + 2.2e-3 * math.exp(-((vin - center) / (0.18 * vdd)) ** 2)
            points.append(
                TC4069UBPPoint(
                    input_voltage_v=vin,
                    output_voltage_v=vout,
                    output_min_v=max(0.0, vout - 0.01 * vdd),
                    output_max_v=min(vdd, vout + 0.01 * vdd),
                    output_rms_v=abs(vout),
                    supply_current_a=supply_current,
                    input_current_a=abs(vin) / 1_000_000.0,
                )
            )

        self.report_progress("TC4069UBP simulated sweep complete", 1.0)
        return points

    def _sweep_values(self) -> list[float]:
        cfg = self.config
        values: list[float] = []
        v = cfg.input_start_v
        direction = 1.0 if cfg.input_step_v > 0.0 else -1.0
        epsilon = abs(cfg.input_step_v) * 1e-9
        while direction * (v - cfg.input_stop_v) <= epsilon:
            values.append(round(v, 12))
            v += cfg.input_step_v
            if len(values) > 10_000:
                raise ValueError("TC4069UBP sweep would generate too many points")
        if values and abs(values[-1] - cfg.input_stop_v) > epsilon:
            values.append(round(cfg.input_stop_v, 12))
        return values

    def _point_from_waveform(
        self,
        *,
        input_voltage_v: float,
        waveform_v: list[float],
        supply_current_a: float | None,
        input_current_a: float | None,
    ) -> TC4069UBPPoint:
        if not waveform_v:
            raise RuntimeError("Oscilloscope returned an empty waveform")
        mean_v = sum(waveform_v) / len(waveform_v)
        rms_v = math.sqrt(sum(v * v for v in waveform_v) / len(waveform_v))
        return TC4069UBPPoint(
            input_voltage_v=input_voltage_v,
            output_voltage_v=mean_v,
            output_min_v=min(waveform_v),
            output_max_v=max(waveform_v),
            output_rms_v=rms_v,
            supply_current_a=supply_current_a,
            input_current_a=input_current_a,
        )

    def _estimate_threshold(self, points: list[TC4069UBPPoint]) -> float | None:
        if not points:
            return None
        target = self.config.supply_voltage_v * self.config.output_threshold_ratio
        sorted_points = sorted(points, key=lambda point: point.input_voltage_v)
        for left, right in zip(sorted_points, sorted_points[1:]):
            left_delta = left.output_voltage_v - target
            right_delta = right.output_voltage_v - target
            if left_delta == 0.0:
                return left.input_voltage_v
            if left_delta * right_delta <= 0.0:
                span = right.output_voltage_v - left.output_voltage_v
                if span == 0.0:
                    return left.input_voltage_v
                ratio = (target - left.output_voltage_v) / span
                return left.input_voltage_v + ratio * (
                    right.input_voltage_v - left.input_voltage_v
                )
        return min(
            sorted_points,
            key=lambda point: abs(point.output_voltage_v - target),
        ).input_voltage_v

    def _measure_current(
        self, supply: IDCSupply, channel: InstrumentChannel
    ) -> float | None:
        measure = getattr(supply, "measure_current", None)
        if not callable(measure):
            return None
        try:
            return float(measure(channel))
        except Exception:
            logger.debug("Current measurement unavailable on %r", channel, exc_info=True)
            return None

    def _enable_supply_output(
        self, channel: InstrumentChannel, enabled: bool
    ) -> None:
        if self.dc_supply is None:
            return
        enable = getattr(self.dc_supply, "enable_output", None)
        if callable(enable):
            enable(channel, enabled=enabled)


@dataclass
class InductorCharacterization(BaseExperiment):
    """Characterize an inductor with an SR860/SR865 impedance frequency sweep.

    The experiment composes the existing ``IImpedanceAnalyzer`` contract instead
    of speaking SCPI directly. When no analyzer is injected it creates an
    ``SR860Backend`` lazily, preserving standalone backend compatibility. In
    simulation mode OpenBench owns a simulated SR860 backend and configures its
    DUT model from ``InductorCharacterizationConfig``.

    Attributes:
        name: Human-readable experiment identifier.
        config: Sweep and measurement configuration.
        impedance_analyzer: Optional impedance analyzer adapter. If omitted, an
            ``SR860Backend`` is created in ``setup()``.
        simulate: When True, run the full experiment without touching hardware.
    """

    config: InductorCharacterizationConfig = field(
        default_factory=InductorCharacterizationConfig
    )
    impedance_analyzer: IImpedanceAnalyzer | None = field(default=None, repr=False)

    _owns_analyzer: bool = field(default=False, init=False, repr=False)

    def validate(self) -> None:
        """Validate sweep, excitation, and simulated DUT parameters.

        Raises:
            ValueError: If the configured characterization cannot be run safely.
        """
        cfg = self.config
        if cfg.start_hz <= 0.0 or cfg.stop_hz <= 0.0:
            raise ValueError("start_hz and stop_hz must be positive")
        if cfg.stop_hz <= cfg.start_hz:
            raise ValueError("stop_hz must be greater than start_hz")
        if cfg.start_hz < _SR860_MIN_SWEEP_HZ or cfg.stop_hz > _SR860_MAX_SWEEP_HZ:
            raise ValueError(
                "SR860 sweep frequency range must stay within "
                f"{_SR860_MIN_SWEEP_HZ:g} Hz to {_SR860_MAX_SWEEP_HZ:g} Hz"
            )
        if cfg.num_points < 2:
            raise ValueError("num_points must be >= 2")
        if cfg.num_points > 10_000:
            raise ValueError("num_points is too large")
        if cfg.excitation_v <= 0.0:
            raise ValueError("excitation_v must be positive")
        if cfg.settle_periods < 0:
            raise ValueError("settle_periods must be >= 0")
        if cfg.time_constant_s <= 0.0:
            raise ValueError("time_constant_s must be positive")
        if cfg.series_resistor_ohm <= 0.0:
            raise ValueError("series_resistor_ohm must be positive")
        if cfg.source_series_ohm < 0.0:
            raise ValueError("source_series_ohm must be >= 0")
        if cfg.nominal_inductance_h is not None and cfg.nominal_inductance_h <= 0.0:
            raise ValueError("nominal_inductance_h must be positive when provided")
        if cfg.simulation_series_resistance_ohm < 0.0:
            raise ValueError("simulation_series_resistance_ohm must be >= 0")
        if cfg.simulation_inductance_h <= 0.0:
            raise ValueError("simulation_inductance_h must be positive")

    def setup(self) -> None:
        """Connect and configure the SR860-compatible impedance analyzer."""
        cfg = self.config
        if (
            self._simulate
            and self.impedance_analyzer is not None
            and not getattr(self.impedance_analyzer, "simulate", False)
        ):
            logger.info("Ignoring injected hardware analyzer for simulation mode")
            self.impedance_analyzer = None
            self._owns_analyzer = False

        if self.impedance_analyzer is None:
            from openbench.backends.sr860_backend import SR860Backend

            self.impedance_analyzer = SR860Backend(
                name="sr860-inductor",
                simulate=self._simulate,
                series_resistor_ohm=cfg.series_resistor_ohm,
                source_series_ohm=cfg.source_series_ohm,
                excitation_v=cfg.excitation_v,
                time_constant_s=cfg.time_constant_s,
            )
            self._owns_analyzer = True

        self.impedance_analyzer.connect()
        self._configure_analyzer()
        logger.info("Inductor characterization analyzer configured")

    def _run(self) -> dict[str, Any]:
        """Execute the impedance sweep and derive inductor metrics.

        Returns:
            Dictionary containing raw points, derived summary, and sweep metadata.
        """
        analyzer = self.impedance_analyzer
        if analyzer is None:
            raise RuntimeError("An impedance analyzer is required")

        cfg = self.config
        settle_periods = 0 if self._simulate else cfg.settle_periods
        self.report_progress("Starting SR860 inductor sweep", 0.0)
        if self._abort_requested:
            raise RuntimeError("aborted")

        impedance_points = analyzer.sweep(
            cfg.start_hz,
            cfg.stop_hz,
            cfg.num_points,
            excitation_v=cfg.excitation_v,
            log_scale=cfg.log_scale,
            settle_periods=settle_periods,
        )
        if len(impedance_points) != cfg.num_points:
            logger.warning(
                "Inductor sweep returned %d/%d points",
                len(impedance_points),
                cfg.num_points,
            )

        points = [self._point_from_impedance(point) for point in impedance_points]
        summary = self._summarize_points(points)
        self.report_progress("SR860 inductor sweep complete", 1.0)

        return {
            "component": "inductor",
            "points": [point.__dict__ for point in points],
            "summary": summary,
            "point_count": len(points),
            "simulated": self._simulate,
            "sweep": {
                "start_hz": cfg.start_hz,
                "stop_hz": cfg.stop_hz,
                "num_points": cfg.num_points,
                "log_scale": cfg.log_scale,
                "excitation_v": cfg.excitation_v,
                "settle_periods": settle_periods,
            },
        }

    def teardown(self) -> None:
        """Disconnect an analyzer created by this experiment."""
        if self.impedance_analyzer is not None and self._owns_analyzer:
            try:
                self.impedance_analyzer.disconnect()
            except Exception:
                logger.warning(
                    "Could not disconnect owned analyzer %s",
                    self.impedance_analyzer.name,
                    exc_info=True,
                )

    def _configure_analyzer(self) -> None:
        cfg = self.config
        analyzer = self.impedance_analyzer
        if analyzer is None:
            return

        set_time_constant = getattr(analyzer, "set_time_constant", None)
        if callable(set_time_constant):
            set_time_constant(cfg.time_constant_s)

        set_excitation = getattr(analyzer, "set_excitation", None)
        if callable(set_excitation):
            set_excitation(cfg.excitation_v)

        if self._simulate:
            set_sim_component = getattr(analyzer, "set_sim_component", None)
            if callable(set_sim_component):
                set_sim_component(
                    cfg.simulation_series_resistance_ohm,
                    l_h=cfg.simulation_inductance_h,
                )

    def _point_from_impedance(self, point: ImpedancePoint) -> InductorCharacterizationPoint:
        omega = 2.0 * math.pi * point.frequency_hz
        inductance_h = point.z_imag_ohm / omega if omega > 0.0 else math.nan
        esr_ohm = point.z_real_ohm
        quality_factor = point.z_imag_ohm / esr_ohm if esr_ohm > 0.0 else None
        return InductorCharacterizationPoint(
            frequency_hz=point.frequency_hz,
            z_real_ohm=point.z_real_ohm,
            z_imag_ohm=point.z_imag_ohm,
            magnitude_ohm=point.magnitude_ohm,
            phase_deg=point.phase_deg,
            equivalent_series_resistance_ohm=esr_ohm,
            inductance_h=inductance_h,
            quality_factor=quality_factor,
            is_inductive=point.z_imag_ohm > 0.0,
            metadata=dict(point.metadata),
        )

    def _summarize_points(self, points: list[InductorCharacterizationPoint]) -> dict[str, Any]:
        inductive = [
            point
            for point in points
            if point.is_inductive and math.isfinite(point.inductance_h)
        ]
        inductances = [point.inductance_h for point in inductive]
        series_resistances = [
            point.equivalent_series_resistance_ohm
            for point in points
            if math.isfinite(point.equivalent_series_resistance_ohm)
        ]
        quality_factors = [
            point.quality_factor
            for point in inductive
            if point.quality_factor is not None and math.isfinite(point.quality_factor)
        ]

        summary: dict[str, Any] = {
            "inductive_point_count": len(inductive),
            "inductance_h_mean": self._mean(inductances),
            "inductance_h_median": self._median(inductances),
            "inductance_h_std": self._stdev(inductances),
            "series_resistance_ohm_mean": self._mean(series_resistances),
            "series_resistance_ohm_median": self._median(series_resistances),
            "quality_factor_mean": self._mean(quality_factors),
            "quality_factor_max": max(quality_factors) if quality_factors else None,
            "self_resonance_hz": self._estimate_self_resonance(points),
        }

        nominal = self.config.nominal_inductance_h
        measured = summary["inductance_h_median"]
        if nominal is not None and measured is not None:
            summary["nominal_inductance_h"] = nominal
            summary["nominal_error_percent"] = 100.0 * (measured - nominal) / nominal

        return summary

    def _estimate_self_resonance(
        self, points: list[InductorCharacterizationPoint]
    ) -> float | None:
        ordered = sorted(points, key=lambda point: point.frequency_hz)
        for left, right in zip(ordered, ordered[1:]):
            if left.z_imag_ohm == 0.0:
                return left.frequency_hz
            if left.z_imag_ohm * right.z_imag_ohm <= 0.0:
                span = right.z_imag_ohm - left.z_imag_ohm
                if span == 0.0:
                    return left.frequency_hz
                ratio = -left.z_imag_ohm / span
                return left.frequency_hz + ratio * (
                    right.frequency_hz - left.frequency_hz
                )
        return None

    def _mean(self, values: list[float]) -> float | None:
        return statistics.fmean(values) if values else None

    def _median(self, values: list[float]) -> float | None:
        return statistics.median(values) if values else None

    def _stdev(self, values: list[float]) -> float | None:
        if not values:
            return None
        if len(values) == 1:
            return 0.0
        return statistics.stdev(values)


__all__ = [
    "InductorCharacterization",
    "InductorCharacterizationConfig",
    "InductorCharacterizationPoint",
    "TC4069UBPCharacterization",
    "TC4069UBPCharacterizationConfig",
    "TC4069UBPPoint",
    "logger",
]
