"""Tests for VirtualBench backend adapters (simulation mode only)."""

from __future__ import annotations

import pytest

from openbench.backends.virtualbench_backend import (
    VirtualBenchFGenBackend,
    VirtualBenchOscilloscopeBackend,
    VirtualBenchPSBackend,
    _normalize_mso_channel,
    _normalize_ps_channel,
)
from openbench.core.interfaces import (
    DCSweepReading,
    FrequencySweepPoint,
    IDCSupply,
    IFunctionGenerator,
    IOscilloscope,
    InstrumentStatus,
    OscilloscopeReading,
    WaveformConfig,
)


# ---------------------------------------------------------------------------
# Channel normalization helpers
# ---------------------------------------------------------------------------


def test_normalize_mso_channel_integer() -> None:
    assert _normalize_mso_channel(1) == "mso/1"
    assert _normalize_mso_channel(2) == "mso/2"


def test_normalize_mso_channel_string_forms() -> None:
    assert _normalize_mso_channel("CH1") == "mso/1"
    assert _normalize_mso_channel("ch2") == "mso/2"
    assert _normalize_mso_channel("mso/1") == "mso/1"


def test_normalize_mso_channel_invalid_raises() -> None:
    with pytest.raises(ValueError):
        _normalize_mso_channel(9)


def test_normalize_ps_channel_integer() -> None:
    assert _normalize_ps_channel(1) == "ps/+25V"
    assert _normalize_ps_channel(2) == "ps/-25V"
    assert _normalize_ps_channel(3) == "ps/+6V"


def test_normalize_ps_channel_string_forms() -> None:
    assert _normalize_ps_channel("+25V") == "ps/+25V"
    assert _normalize_ps_channel("NEG") == "ps/-25V"
    assert _normalize_ps_channel("ps/+6V") == "ps/+6V"


def test_normalize_ps_channel_invalid_raises() -> None:
    with pytest.raises(ValueError):
        _normalize_ps_channel("UNKNOWN")


# ---------------------------------------------------------------------------
# VirtualBenchOscilloscopeBackend — interface contract
# ---------------------------------------------------------------------------


def make_osc(**kwargs: object) -> VirtualBenchOscilloscopeBackend:
    return VirtualBenchOscilloscopeBackend(name="vb-osc-test", simulate=True, **kwargs)


def test_osc_implements_ioscilloscope() -> None:
    assert isinstance(make_osc(), IOscilloscope)


def test_osc_connect_sets_simulated_status() -> None:
    osc = make_osc()
    osc.connect()
    assert osc.status() == InstrumentStatus.SIMULATED


def test_osc_disconnect_sets_disconnected_status() -> None:
    osc = make_osc()
    osc.connect()
    osc.disconnect()
    assert osc.status() == InstrumentStatus.DISCONNECTED


def test_osc_context_manager_lifecycle() -> None:
    osc = make_osc()
    with osc:
        assert osc.status() == InstrumentStatus.SIMULATED
    assert osc.status() == InstrumentStatus.DISCONNECTED


def test_osc_connect_is_idempotent() -> None:
    osc = make_osc()
    osc.connect()
    osc.connect()
    assert osc.status() == InstrumentStatus.SIMULATED


def test_osc_configure_channel_and_acquire() -> None:
    with make_osc() as osc:
        osc.configure_channel(1, volts_per_div=1.0, coupling="DC")
        reading = osc.acquire(1)

    assert isinstance(reading, OscilloscopeReading)
    assert reading.channel == 1
    assert len(reading.time_s) >= 512
    assert len(reading.voltage_v) == len(reading.time_s)
    assert reading.sample_rate_hz > 0


def test_osc_acquire_channel_2() -> None:
    with make_osc() as osc:
        osc.configure_channel(2, volts_per_div=0.5)
        reading = osc.acquire(2)

    assert reading.channel == 2
    assert len(reading.voltage_v) >= 512


def test_osc_configure_timebase_affects_duration() -> None:
    with make_osc() as osc:
        osc.configure_timebase(1e-4)
        r_fast = osc.acquire(1)
        osc.configure_timebase(1e-2)
        r_slow = osc.acquire(1)

    # Slower timebase → more samples (same sample rate, longer window)
    assert len(r_slow.time_s) > len(r_fast.time_s)


def test_osc_acquire_before_connect_raises() -> None:
    osc = make_osc()
    with pytest.raises(RuntimeError, match="not connected"):
        osc.acquire(1)


def test_osc_channel_string_identifiers() -> None:
    with make_osc() as osc:
        osc.configure_channel("CH1", volts_per_div=2.0)
        reading = osc.acquire("CH1")
    assert reading.channel == "CH1"


def test_osc_invalid_channel_raises() -> None:
    with make_osc() as osc:
        with pytest.raises(ValueError):
            osc.configure_channel(5, volts_per_div=1.0)


def test_osc_sim_frequency_parameter() -> None:
    with VirtualBenchOscilloscopeBackend(
        name="vb-osc", simulate=True, sim_frequency_hz=500.0, sim_amplitude_v=2.0
    ) as osc:
        reading = osc.acquire(1)
    assert max(reading.voltage_v) > 1.0


# ---------------------------------------------------------------------------
# VirtualBenchFGenBackend — interface contract
# ---------------------------------------------------------------------------


def make_fgen(**kwargs: object) -> VirtualBenchFGenBackend:
    return VirtualBenchFGenBackend(name="vb-fgen-test", simulate=True, **kwargs)


def test_fgen_implements_ifunctiongenerator() -> None:
    assert isinstance(make_fgen(), IFunctionGenerator)


def test_fgen_connect_sets_simulated_status() -> None:
    fgen = make_fgen()
    fgen.connect()
    assert fgen.status() == InstrumentStatus.SIMULATED


def test_fgen_context_manager_lifecycle() -> None:
    fgen = make_fgen()
    with fgen:
        assert fgen.status() == InstrumentStatus.SIMULATED
    assert fgen.status() == InstrumentStatus.DISCONNECTED


def test_fgen_configure_does_not_raise() -> None:
    with make_fgen() as fgen:
        cfg = WaveformConfig(waveform="sine", frequency_hz=1_000.0, amplitude_v=1.0)
        fgen.configure(cfg)


def test_fgen_enable_output_does_not_raise() -> None:
    with make_fgen() as fgen:
        fgen.enable_output(1, enabled=True)
        fgen.enable_output(1, enabled=False)


def test_fgen_sweep_returns_frequency_setpoints() -> None:
    with make_fgen() as fgen:
        points = fgen.sweep(1, 100.0, 10_000.0, 5, 1.0, log_scale=True, dwell_s=0.0)

    assert len(points) == 5
    assert all(isinstance(p, FrequencySweepPoint) for p in points)
    freqs = [p.frequency_hz for p in points]
    assert freqs[0] == pytest.approx(100.0, rel=1e-6)
    assert freqs[-1] == pytest.approx(10_000.0, rel=1e-6)


def test_fgen_sweep_linear_spacing() -> None:
    with make_fgen() as fgen:
        points = fgen.sweep(1, 0.0, 1000.0, 11, 1.0, log_scale=False, dwell_s=0.0)

    assert len(points) == 11
    freqs = [p.frequency_hz for p in points]
    assert freqs[0] == pytest.approx(0.0, abs=1e-6)
    assert freqs[5] == pytest.approx(500.0, rel=1e-6)


def test_fgen_sweep_too_few_points_raises() -> None:
    with make_fgen() as fgen:
        with pytest.raises(ValueError):
            fgen.sweep(1, 100.0, 1000.0, 1, 1.0)


def test_fgen_configure_before_connect_raises() -> None:
    fgen = make_fgen()
    with pytest.raises(RuntimeError, match="not connected"):
        fgen.configure(WaveformConfig(waveform="sine", frequency_hz=1000.0, amplitude_v=1.0))


# ---------------------------------------------------------------------------
# VirtualBenchPSBackend — interface contract
# ---------------------------------------------------------------------------


def make_ps(**kwargs: object) -> VirtualBenchPSBackend:
    return VirtualBenchPSBackend(name="vb-ps-test", simulate=True, **kwargs)


def test_ps_implements_idcsupply() -> None:
    assert isinstance(make_ps(), IDCSupply)


def test_ps_connect_sets_simulated_status() -> None:
    ps = make_ps()
    ps.connect()
    assert ps.status() == InstrumentStatus.SIMULATED


def test_ps_context_manager_lifecycle() -> None:
    ps = make_ps()
    with ps:
        assert ps.status() == InstrumentStatus.SIMULATED
    assert ps.status() == InstrumentStatus.DISCONNECTED


def test_ps_set_voltage_positive_rail() -> None:
    with make_ps() as ps:
        ps.set_voltage(1, 12.0)
        ps.enable_outputs(enabled=True)
        v = ps.measure_voltage(1)
    assert v == pytest.approx(12.0, abs=0.01)


def test_ps_set_voltage_negative_rail() -> None:
    with make_ps() as ps:
        ps.set_voltage(2, -10.0)
        ps.enable_outputs(enabled=True)
        v = ps.measure_voltage(2)
    assert v == pytest.approx(-10.0, abs=0.01)


def test_ps_set_current_updates_limit() -> None:
    with make_ps() as ps:
        ps.set_current(1, 0.05)


def test_ps_set_voltage_invalid_channel_raises() -> None:
    with make_ps() as ps:
        with pytest.raises(ValueError):
            ps.set_voltage(99, 1.0)


def test_ps_sweep_returns_dc_sweep_readings() -> None:
    with make_ps() as ps:
        readings = ps.sweep(1, 0.0, 5.0, 1.0)

    assert len(readings) >= 2
    assert all(isinstance(r, DCSweepReading) for r in readings)


def test_ps_sweep_readings_have_channel_label() -> None:
    with make_ps() as ps:
        readings = ps.sweep("+25V", 0.0, 3.0, 1.0)

    assert all(r.channel == "ps/+25V" for r in readings)


def test_ps_sweep_with_current_limit() -> None:
    with make_ps() as ps:
        readings = ps.sweep(1, 0.0, 2.0, 0.5, current_limit_a=0.1)

    assert all(r.current_limit_a == pytest.approx(0.1) for r in readings)


def test_ps_sweep_zero_step_raises() -> None:
    with make_ps() as ps:
        with pytest.raises(ValueError, match="step_v"):
            ps.sweep(1, 0.0, 1.0, 0.0)


def test_ps_sweep_reverse_direction() -> None:
    with make_ps() as ps:
        readings = ps.sweep(1, 5.0, 0.0, 1.0)

    setpoints = [r.voltage_setpoint_v for r in readings]
    assert setpoints[0] >= setpoints[-1]


def test_ps_sweep_before_connect_raises() -> None:
    ps = make_ps()
    with pytest.raises(RuntimeError, match="not connected"):
        ps.sweep(1, 0.0, 1.0, 0.5)


def test_ps_channel_aliases_resolve() -> None:
    with make_ps() as ps:
        ps.set_voltage("POS", 15.0)
        ps.set_voltage("NEG", -15.0)
        ps.set_voltage("6V", 5.0)
        ps.enable_outputs(enabled=True)
        assert ps.measure_voltage("ps/+25V") == pytest.approx(15.0, abs=0.01)
        assert ps.measure_voltage("ps/-25V") == pytest.approx(-15.0, abs=0.01)
        assert ps.measure_voltage("ps/+6V") == pytest.approx(5.0, abs=0.01)
