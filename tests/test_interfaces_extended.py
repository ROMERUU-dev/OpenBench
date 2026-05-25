"""Tests for IOscilloscope, IFunctionGenerator, and IImpedanceAnalyzer."""

from __future__ import annotations

import pytest

from openbench.core.interfaces import (
    FrequencySweepPoint,
    IFunctionGenerator,
    IImpedanceAnalyzer,
    IOscilloscope,
    ImpedancePoint,
    InstrumentChannel,
    InstrumentStatus,
    OscilloscopeReading,
    WaveformConfig,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class StubFunctionGenerator(IFunctionGenerator):
    """Minimal function generator for contract tests."""

    def __post_init__(self) -> None:
        self.applied: list[WaveformConfig] = []
        self.output_enabled: dict[InstrumentChannel, bool] = {}
        self.sweep_log: list[tuple[float, float]] = []

    def _connect(self) -> None:
        pass

    def _disconnect(self) -> None:
        pass

    def configure(self, config: WaveformConfig) -> None:
        self.applied.append(config)

    def enable_output(self, channel: InstrumentChannel = 1, *, enabled: bool = True) -> None:
        self.output_enabled[channel] = enabled

    def sweep(
        self,
        channel: InstrumentChannel,
        start_hz: float,
        stop_hz: float,
        num_points: int,
        amplitude_v: float,
        *,
        log_scale: bool = True,
        dwell_s: float = 0.1,
        waveform: str = "sine",
    ) -> list[FrequencySweepPoint]:
        self.sweep_log.append((start_hz, stop_hz))
        return [FrequencySweepPoint(frequency_hz=start_hz, channel=channel)]


class StubOscilloscope(IOscilloscope):
    """Minimal oscilloscope for contract tests."""

    def __post_init__(self) -> None:
        self.channels_configured: list[InstrumentChannel] = []
        self.timebase_configured: bool = False

    def _connect(self) -> None:
        pass

    def _disconnect(self) -> None:
        pass

    def configure_channel(
        self,
        channel: InstrumentChannel,
        *,
        volts_per_div: float,
        coupling: str = "DC",
        enabled: bool = True,
    ) -> None:
        self.channels_configured.append(channel)

    def configure_timebase(
        self,
        time_per_div_s: float,
        *,
        trigger_level_v: float = 0.0,
        trigger_channel: InstrumentChannel = 1,
        trigger_slope: str = "rising",
    ) -> None:
        self.timebase_configured = True

    def acquire(self, channel: InstrumentChannel) -> OscilloscopeReading:
        n = 100
        dt = 1e-6
        return OscilloscopeReading(
            channel=channel,
            time_s=[i * dt for i in range(n)],
            voltage_v=[0.0] * n,
            sample_rate_hz=1.0 / dt,
        )


class StubImpedanceAnalyzer(IImpedanceAnalyzer):
    """Minimal impedance analyzer for contract tests."""

    def __post_init__(self) -> None:
        self.measured_freqs: list[float] = []

    def _connect(self) -> None:
        pass

    def _disconnect(self) -> None:
        pass

    def measure_at_freq(
        self,
        frequency_hz: float,
        *,
        excitation_v: float | None = None,
        settle_periods: int = 5,
    ) -> ImpedancePoint:
        self.measured_freqs.append(frequency_hz)
        return ImpedancePoint(
            frequency_hz=frequency_hz,
            z_real_ohm=100.0,
            z_imag_ohm=0.0,
            phase_deg=0.0,
            magnitude_ohm=100.0,
        )

    def sweep(
        self,
        start_hz: float,
        stop_hz: float,
        num_points: int,
        *,
        excitation_v: float | None = None,
        log_scale: bool = True,
        settle_periods: int = 5,
    ) -> list[ImpedancePoint]:
        return [self.measure_at_freq(start_hz), self.measure_at_freq(stop_hz)]


# ---------------------------------------------------------------------------
# IFunctionGenerator
# ---------------------------------------------------------------------------


def test_function_generator_configure_stores_waveform() -> None:
    """configure() applies and records the WaveformConfig."""
    gen = StubFunctionGenerator(name="fg", simulate=True)
    cfg = WaveformConfig(waveform="sine", frequency_hz=1000.0, amplitude_v=1.0)
    gen.configure(cfg)
    assert gen.applied == [cfg]


def test_function_generator_enable_output() -> None:
    """enable_output() toggles channel state."""
    gen = StubFunctionGenerator(name="fg", simulate=True)
    gen.enable_output(1, enabled=True)
    gen.enable_output(2, enabled=False)
    assert gen.output_enabled == {1: True, 2: False}


def test_function_generator_sweep_returns_points() -> None:
    """sweep() returns at least one FrequencySweepPoint."""
    gen = StubFunctionGenerator(name="fg", simulate=True)
    pts = gen.sweep(1, 100.0, 10000.0, 10, 1.0)
    assert len(pts) >= 1
    assert all(isinstance(p, FrequencySweepPoint) for p in pts)


def test_waveform_config_defaults() -> None:
    """WaveformConfig has sensible defaults for optional fields."""
    cfg = WaveformConfig(waveform="square", frequency_hz=500.0, amplitude_v=3.3)
    assert cfg.offset_v == 0.0
    assert cfg.phase_deg == 0.0
    assert cfg.duty_cycle == 0.5
    assert cfg.channel == 1


# ---------------------------------------------------------------------------
# IOscilloscope
# ---------------------------------------------------------------------------


def test_oscilloscope_configure_channel_records_channel() -> None:
    """configure_channel() registers the channel as configured."""
    scope = StubOscilloscope(name="scope", simulate=True)
    scope.configure_channel(1, volts_per_div=0.5)
    scope.configure_channel(2, volts_per_div=1.0, coupling="AC")
    assert scope.channels_configured == [1, 2]


def test_oscilloscope_configure_timebase() -> None:
    """configure_timebase() marks timebase as configured."""
    scope = StubOscilloscope(name="scope", simulate=True)
    scope.configure_timebase(1e-3, trigger_level_v=0.5)
    assert scope.timebase_configured is True


def test_oscilloscope_acquire_returns_reading() -> None:
    """acquire() returns an OscilloscopeReading with aligned time/voltage."""
    scope = StubOscilloscope(name="scope", simulate=True)
    reading = scope.acquire(1)
    assert reading.channel == 1
    assert len(reading.time_s) == len(reading.voltage_v)
    assert reading.sample_rate_hz > 0


# ---------------------------------------------------------------------------
# IImpedanceAnalyzer
# ---------------------------------------------------------------------------


def test_impedance_analyzer_measure_at_freq() -> None:
    """measure_at_freq() returns an ImpedancePoint at the requested frequency."""
    zia = StubImpedanceAnalyzer(name="sr860", simulate=True)
    pt = zia.measure_at_freq(1000.0)
    assert pt.frequency_hz == 1000.0
    assert isinstance(pt, ImpedancePoint)


def test_impedance_analyzer_sweep_returns_multiple_points() -> None:
    """sweep() returns a list of ImpedancePoints."""
    zia = StubImpedanceAnalyzer(name="sr860", simulate=True)
    pts = zia.sweep(100.0, 10000.0, 10)
    assert len(pts) >= 2
    assert all(isinstance(p, ImpedancePoint) for p in pts)


def test_impedance_point_fields() -> None:
    """ImpedancePoint exposes real, imaginary, phase, and magnitude."""
    pt = ImpedancePoint(
        frequency_hz=500.0,
        z_real_ohm=75.0,
        z_imag_ohm=-25.0,
        phase_deg=-18.43,
        magnitude_ohm=79.06,
        metadata={"time_constant_s": 0.1},
    )
    assert pt.z_real_ohm == 75.0
    assert pt.metadata["time_constant_s"] == 0.1


def test_interfaces_respect_instrument_lifecycle() -> None:
    """All new interfaces support simulated connect/disconnect lifecycle."""
    instruments = [
        StubFunctionGenerator(name="fg", simulate=True),
        StubOscilloscope(name="scope", simulate=True),
        StubImpedanceAnalyzer(name="zia", simulate=True),
    ]
    for inst in instruments:
        inst.connect()
        assert inst.status() == InstrumentStatus.SIMULATED
        inst.disconnect()
        assert inst.status() == InstrumentStatus.DISCONNECTED
