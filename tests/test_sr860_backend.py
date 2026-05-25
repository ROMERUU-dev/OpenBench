"""Tests for SR860Backend adapter (simulation mode only)."""

from __future__ import annotations

import math

import pytest

from openbench.backends.sr860_backend import (
    SR860Backend,
    _StubSR860Controller,
    _build_impedance_point,
    _series_divider_impedance,
    _source_phasor,
)
from openbench.core.interfaces import IImpedanceAnalyzer, ImpedancePoint, InstrumentStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_backend(**kwargs: object) -> SR860Backend:
    """Return a simulated SR860Backend ready to use."""
    return SR860Backend(name="sr860-test", simulate=True, **kwargs)


# ---------------------------------------------------------------------------
# Impedance math unit tests
# ---------------------------------------------------------------------------


def test_source_phasor_zero_phase() -> None:
    """At PHAS=0 the source phasor is real and positive."""
    phasor = _source_phasor(1.0, 0.0)
    assert phasor.real == pytest.approx(1.0, rel=1e-9)
    assert abs(phasor.imag) < 1e-12


def test_source_phasor_90_degrees() -> None:
    """At PHAS=90° the source phasor points along −jY (PHAS rotates reference)."""
    phasor = _source_phasor(1.0, 90.0)
    # phase_rad = -pi/2  →  cos=-0, sin=-1  →  phasor = -j
    assert abs(phasor.real) < 1e-12
    assert phasor.imag == pytest.approx(-1.0, rel=1e-9)


def test_series_divider_resistor() -> None:
    """Pure resistor DUT: two equal Rs gives Z_DUT = Rs."""
    rs = 100.0
    # With Z_DUT = Rs and V_source = V_dut * 2 the result should be Rs
    source_v = complex(2.0, 0.0)
    dut_v = complex(1.0, 0.0)
    z = _series_divider_impedance(rs, source_v, dut_v)
    assert z.real == pytest.approx(rs, rel=1e-9)
    assert abs(z.imag) < 1e-9


def test_series_divider_degenerate_raises() -> None:
    """ZeroDivisionError when source ≈ DUT voltage."""
    with pytest.raises(ZeroDivisionError):
        _series_divider_impedance(100.0, complex(1.0, 0.0), complex(1.0, 0.0))


def test_build_impedance_point_fields() -> None:
    """_build_impedance_point returns correct magnitude and phase fields."""
    # Purely inductive DUT at 1 kHz: L=44.4 mH → X=2π*1000*44.4e-3≈279 Ω
    f = 1_000.0
    omega = 2 * math.pi * f
    l = 44.4e-3
    r_dut = 10.0
    z_expected = complex(r_dut, omega * l)
    rs = 270.0  # 220+50

    v_src = _source_phasor(1.0, 0.0)
    v_dut = v_src * z_expected / (rs + z_expected)
    pt = _build_impedance_point(v_dut.real, v_dut.imag, f, 1.0, 0.0, rs)

    assert isinstance(pt, ImpedancePoint)
    assert pt.frequency_hz == pytest.approx(f, rel=1e-9)
    assert pt.z_real_ohm == pytest.approx(r_dut, rel=1e-2)
    assert pt.z_imag_ohm == pytest.approx(omega * l, rel=1e-2)
    assert pt.magnitude_ohm == pytest.approx(abs(z_expected), rel=1e-2)


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


def test_backend_implements_iimpedanceanalyzer() -> None:
    assert isinstance(make_backend(), IImpedanceAnalyzer)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_connect_sets_simulated_status() -> None:
    b = make_backend()
    b.connect()
    assert b.status() == InstrumentStatus.SIMULATED


def test_disconnect_sets_disconnected_status() -> None:
    b = make_backend()
    b.connect()
    b.disconnect()
    assert b.status() == InstrumentStatus.DISCONNECTED


def test_context_manager_lifecycle() -> None:
    b = make_backend()
    with b:
        assert b.status() == InstrumentStatus.SIMULATED
    assert b.status() == InstrumentStatus.DISCONNECTED


def test_connect_is_idempotent() -> None:
    b = make_backend()
    b.connect()
    b.connect()
    assert b.status() == InstrumentStatus.SIMULATED


# ---------------------------------------------------------------------------
# measure_at_freq
# ---------------------------------------------------------------------------


def test_measure_at_freq_returns_impedance_point() -> None:
    with make_backend() as b:
        pt = b.measure_at_freq(1_000.0, settle_periods=0)

    assert isinstance(pt, ImpedancePoint)
    assert pt.frequency_hz == pytest.approx(1_000.0, rel=1e-9)
    assert math.isfinite(pt.magnitude_ohm)
    assert math.isfinite(pt.phase_deg)


def test_measure_at_freq_magnitude_positive() -> None:
    with make_backend() as b:
        pt = b.measure_at_freq(500.0, settle_periods=0)

    assert pt.magnitude_ohm > 0.0


def test_measure_at_freq_inductive_phase_positive() -> None:
    """Default stub is a coil — phase should be positive (inductive)."""
    with make_backend() as b:
        pt = b.measure_at_freq(10_000.0, settle_periods=0)

    assert pt.phase_deg > 0.0


def test_measure_at_freq_with_excitation_override() -> None:
    with make_backend() as b:
        pt = b.measure_at_freq(1_000.0, excitation_v=0.5, settle_periods=0)

    assert isinstance(pt, ImpedancePoint)


def test_measure_at_freq_frequency_below_min_raises() -> None:
    with make_backend() as b:
        with pytest.raises(ValueError, match="SR860 range"):
            b.measure_at_freq(1e-4, settle_periods=0)


def test_measure_at_freq_frequency_above_max_raises() -> None:
    with make_backend() as b:
        with pytest.raises(ValueError, match="SR860 range"):
            b.measure_at_freq(600_000.0, settle_periods=0)


def test_measure_at_freq_before_connect_raises() -> None:
    b = make_backend()
    with pytest.raises(RuntimeError, match="not connected"):
        b.measure_at_freq(1_000.0)


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def test_sweep_returns_impedance_points() -> None:
    with make_backend() as b:
        pts = b.sweep(100.0, 10_000.0, 5, settle_periods=0)

    assert len(pts) == 5
    assert all(isinstance(pt, ImpedancePoint) for pt in pts)


def test_sweep_points_span_frequency_range() -> None:
    with make_backend() as b:
        pts = b.sweep(100.0, 10_000.0, 5, log_scale=True, settle_periods=0)

    freqs = [pt.frequency_hz for pt in pts]
    assert freqs[0] == pytest.approx(100.0, rel=1e-6)
    assert freqs[-1] == pytest.approx(10_000.0, rel=1e-6)


def test_sweep_linear_spacing() -> None:
    with make_backend() as b:
        pts = b.sweep(100.0, 1_000.0, 10, log_scale=False, settle_periods=0)

    assert len(pts) == 10
    assert pts[0].frequency_hz == pytest.approx(100.0, rel=1e-6)
    assert pts[-1].frequency_hz == pytest.approx(1_000.0, rel=1e-6)


def test_sweep_inductance_increases_with_frequency() -> None:
    """For an inductor, |Z| should increase with frequency."""
    with make_backend() as b:
        pts = b.sweep(100.0, 100_000.0, 10, settle_periods=0)

    magnitudes = [pt.magnitude_ohm for pt in pts]
    assert magnitudes[-1] > magnitudes[0]


def test_sweep_too_few_points_raises() -> None:
    with make_backend() as b:
        with pytest.raises(ValueError, match="num_points"):
            b.sweep(100.0, 1_000.0, 1)


def test_sweep_negative_frequency_raises() -> None:
    with make_backend() as b:
        with pytest.raises(ValueError, match="positive"):
            b.sweep(-100.0, 1_000.0, 5)


def test_sweep_stop_le_start_raises() -> None:
    with make_backend() as b:
        with pytest.raises(ValueError, match="stop_hz"):
            b.sweep(1_000.0, 100.0, 5)


def test_sweep_before_connect_raises() -> None:
    b = make_backend()
    with pytest.raises(RuntimeError, match="not connected"):
        b.sweep(100.0, 1_000.0, 5)


# ---------------------------------------------------------------------------
# set_sim_component
# ---------------------------------------------------------------------------


def test_set_sim_component_capacitor() -> None:
    """Capacitive DUT: phase should be negative."""
    with make_backend() as b:
        b.set_sim_component(5.0, c_f=100e-9)
        pt = b.measure_at_freq(1_000.0, settle_periods=0)

    assert pt.phase_deg < 0.0


def test_set_sim_component_resistor() -> None:
    """Purely resistive DUT: phase should be near 0°."""
    with make_backend() as b:
        b.set_sim_component(220.0, l_h=0.0, c_f=0.0)
        pt = b.measure_at_freq(1_000.0, settle_periods=0)

    assert abs(pt.phase_deg) < 5.0


def test_set_sim_component_noop_on_hardware_mode() -> None:
    """set_sim_component is a no-op when not in simulate mode."""
    b = SR860Backend(name="sr860-noop", simulate=False)
    b.set_sim_component(100.0, l_h=1e-3)  # must not raise


# ---------------------------------------------------------------------------
# SR860-specific helpers
# ---------------------------------------------------------------------------


def test_identify_returns_string() -> None:
    with make_backend() as b:
        idn = b.identify()

    assert isinstance(idn, str)
    assert len(idn) > 0


def test_set_excitation_updates_stored_value() -> None:
    with make_backend() as b:
        b.set_excitation(0.5)
        assert b.excitation_v == pytest.approx(0.5)


def test_set_time_constant_selects_closest() -> None:
    with make_backend() as b:
        b.set_time_constant(0.1)
        assert b.time_constant_s == pytest.approx(0.1, rel=1e-6)


def test_set_time_constant_rounds_to_nearest() -> None:
    with make_backend() as b:
        b.set_time_constant(0.05)
        # Nearest entry is 30 ms or 100 ms — just verify it's a valid table value
        assert b.time_constant_s in (30e-3, 100e-3)


def test_read_xy_returns_two_floats() -> None:
    with make_backend() as b:
        x, y = b.read_xy()

    assert isinstance(x, float)
    assert isinstance(y, float)


# ---------------------------------------------------------------------------
# Stub controller unit tests
# ---------------------------------------------------------------------------


def test_stub_controller_write_and_query_freq() -> None:
    stub = _StubSR860Controller(220.0, 50.0, 1.0)
    stub.write("FREQ 5000")
    assert stub.query("FREQ?") == "5000.0"


def test_stub_controller_write_slvl() -> None:
    stub = _StubSR860Controller(220.0, 50.0, 1.0)
    stub.write("SLVL 0.5")
    assert stub.query("SLVL?") == "0.5"


def test_stub_controller_read_snapshot_returns_finite() -> None:
    stub = _StubSR860Controller(220.0, 50.0, 1.0)
    stub.write("FREQ 1000")
    x, y = stub.read_snapshot_xy()
    assert math.isfinite(x)
    assert math.isfinite(y)
