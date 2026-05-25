"""Tests for KeysightE36312ABackend adapter."""

from __future__ import annotations

import pytest

from openbench.backends.keysight_backend import KeysightE36312ABackend
from openbench.core.interfaces import DCSweepReading, IDCSupply, InstrumentStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_backend(**kwargs: object) -> KeysightE36312ABackend:
    """Return a simulated KeysightE36312ABackend ready to use."""
    return KeysightE36312ABackend(name="keysight-test", simulate=True, **kwargs)


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


def test_backend_implements_idcsupply() -> None:
    """KeysightE36312ABackend is a valid IDCSupply adapter."""
    backend = make_backend()
    assert isinstance(backend, IDCSupply)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_connect_sets_simulated_status() -> None:
    """connect() in simulate mode sets status to SIMULATED."""
    backend = make_backend()
    backend.connect()
    assert backend.status() == InstrumentStatus.SIMULATED


def test_disconnect_sets_disconnected_status() -> None:
    """disconnect() returns adapter to DISCONNECTED state."""
    backend = make_backend()
    backend.connect()
    backend.disconnect()
    assert backend.status() == InstrumentStatus.DISCONNECTED


def test_context_manager_lifecycle() -> None:
    """Context manager connects on enter and disconnects on exit."""
    backend = make_backend()
    with backend:
        assert backend.status() == InstrumentStatus.SIMULATED
    assert backend.status() == InstrumentStatus.DISCONNECTED


def test_connect_is_idempotent() -> None:
    """connect() called twice does not raise or change status."""
    backend = make_backend()
    backend.connect()
    backend.connect()
    assert backend.status() == InstrumentStatus.SIMULATED


# ---------------------------------------------------------------------------
# set_voltage / set_current
# ---------------------------------------------------------------------------


def test_set_voltage_channel_int() -> None:
    """set_voltage accepts an integer channel identifier."""
    with make_backend() as b:
        b.set_voltage(1, 2.5)
        b.enable_output(1, enabled=True)
        assert b.measure_voltage(1) == pytest.approx(2.5, abs=0.01)


def test_set_voltage_channel_str() -> None:
    """set_voltage accepts a 'CH1' string channel identifier."""
    with make_backend() as b:
        b.set_voltage("CH1", 3.0)
        b.enable_output("CH1", enabled=True)
        assert b.measure_voltage("CH1") == pytest.approx(3.0, abs=0.01)


def test_set_current_records_limit() -> None:
    """set_current updates the channel current compliance limit."""
    with make_backend() as b:
        b.set_current("CH2", 0.05)


def test_set_voltage_invalid_channel_raises() -> None:
    """set_voltage raises ValueError for an out-of-range channel."""
    with make_backend() as b:
        with pytest.raises(ValueError):
            b.set_voltage(9, 1.0)


# ---------------------------------------------------------------------------
# enable_output / measure
# ---------------------------------------------------------------------------


def test_enable_output_controls_measurement() -> None:
    """Output must be enabled for measure_voltage to return a non-zero value."""
    with make_backend() as b:
        b.set_voltage(1, 5.0)
        assert b.measure_voltage(1) == pytest.approx(0.0, abs=1e-9)
        b.enable_output(1, enabled=True)
        assert b.measure_voltage(1) == pytest.approx(5.0, abs=0.1)


def test_measure_current_after_output_on() -> None:
    """measure_current returns a positive value after enabling the output."""
    with make_backend() as b:
        b.set_voltage(1, 1.0)
        b.set_current(1, 1.0)
        b.enable_output(1, enabled=True)
        current = b.measure_current(1)
        assert current >= 0.0


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def test_sweep_returns_dc_sweep_readings() -> None:
    """sweep() returns a list of DCSweepReading objects."""
    with make_backend() as b:
        readings = b.sweep(1, 0.0, 1.0, 0.5)
    assert len(readings) >= 2
    assert all(isinstance(r, DCSweepReading) for r in readings)


def test_sweep_readings_have_channel_label() -> None:
    """Each DCSweepReading has the normalized channel label."""
    with make_backend() as b:
        readings = b.sweep("CH1", 0.0, 0.5, 0.25)
    assert all(r.channel == "CH1" for r in readings)


def test_sweep_with_current_limit() -> None:
    """sweep() applies the provided current_limit_a to each reading."""
    with make_backend() as b:
        readings = b.sweep(1, 0.0, 1.0, 0.5, current_limit_a=0.1)
    assert all(r.current_limit_a == pytest.approx(0.1) for r in readings)


def test_sweep_zero_step_raises() -> None:
    """sweep() with step_v=0 raises ValueError."""
    with make_backend() as b:
        with pytest.raises(ValueError, match="step_v"):
            b.sweep(1, 0.0, 1.0, 0.0)


def test_sweep_reverse_direction() -> None:
    """sweep() from high to low voltage produces readings in descending order."""
    with make_backend() as b:
        readings = b.sweep(1, 2.0, 0.0, 0.5)
    setpoints = [r.voltage_setpoint_v for r in readings]
    assert setpoints[0] >= setpoints[-1]


def test_sweep_metadata_contains_compliance_flag() -> None:
    """Each reading metadata dict includes a 'compliance' key."""
    with make_backend() as b:
        readings = b.sweep(1, 0.0, 1.0, 0.5)
    assert all("compliance" in r.metadata for r in readings)


# ---------------------------------------------------------------------------
# set_mock_model
# ---------------------------------------------------------------------------


def test_set_mock_model_diode() -> None:
    """set_mock_model('diode') does not raise in simulate mode."""
    with make_backend() as b:
        b.set_mock_model("diode")


def test_set_mock_model_ignored_when_not_simulated() -> None:
    """set_mock_model is a no-op when _supply is None or not in mock mode."""
    b = make_backend()
    b.set_mock_model("nmos")


# ---------------------------------------------------------------------------
# Require-supply guard
# ---------------------------------------------------------------------------


def test_set_voltage_before_connect_raises() -> None:
    """Calling set_voltage before connect raises RuntimeError."""
    b = make_backend()
    with pytest.raises(RuntimeError, match="not connected"):
        b.set_voltage(1, 1.0)


def test_sweep_before_connect_raises() -> None:
    """Calling sweep before connect raises RuntimeError."""
    b = make_backend()
    with pytest.raises(RuntimeError, match="not connected"):
        b.sweep(1, 0.0, 1.0, 0.5)
