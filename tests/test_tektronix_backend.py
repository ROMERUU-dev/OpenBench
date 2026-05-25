"""Tests for TektronixTBS1000CBackend adapter (simulation mode only)."""

from __future__ import annotations

import pytest

from openbench.backends.tektronix_backend import (
    TektronixTBS1000CBackend,
    _normalize_channel,
)
from openbench.core.interfaces import (
    IOscilloscope,
    InstrumentStatus,
    OscilloscopeReading,
)


# ---------------------------------------------------------------------------
# Channel normalization
# ---------------------------------------------------------------------------


def test_normalize_channel_integer() -> None:
    assert _normalize_channel(1) == "CH1"
    assert _normalize_channel(2) == "CH2"


def test_normalize_channel_short_string() -> None:
    assert _normalize_channel("CH1") == "CH1"
    assert _normalize_channel("CH2") == "CH2"


def test_normalize_channel_lowercase() -> None:
    assert _normalize_channel("ch1") == "CH1"
    assert _normalize_channel("ch2") == "CH2"


def test_normalize_channel_string_digits() -> None:
    assert _normalize_channel("1") == "CH1"
    assert _normalize_channel("2") == "CH2"


def test_normalize_channel_math_and_ref() -> None:
    assert _normalize_channel("MATH") == "MATH"
    assert _normalize_channel("math") == "MATH"
    assert _normalize_channel("REF1") == "REF1"
    assert _normalize_channel("REF2") == "REF2"


def test_normalize_channel_invalid_integer_raises() -> None:
    with pytest.raises(ValueError):
        _normalize_channel(3)

    with pytest.raises(ValueError):
        _normalize_channel(0)


def test_normalize_channel_invalid_string_raises() -> None:
    with pytest.raises(ValueError):
        _normalize_channel("CH5")

    with pytest.raises(ValueError):
        _normalize_channel("xyz")

    with pytest.raises(ValueError):
        _normalize_channel("REF3")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_scope(**kwargs: object) -> TektronixTBS1000CBackend:
    """Return a simulated TektronixTBS1000CBackend ready to use."""
    return TektronixTBS1000CBackend(name="tek-test", simulate=True, **kwargs)


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


def test_backend_implements_ioscilloscope() -> None:
    assert isinstance(make_scope(), IOscilloscope)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_connect_sets_simulated_status() -> None:
    scope = make_scope()
    scope.connect()
    assert scope.status() == InstrumentStatus.SIMULATED


def test_disconnect_sets_disconnected_status() -> None:
    scope = make_scope()
    scope.connect()
    scope.disconnect()
    assert scope.status() == InstrumentStatus.DISCONNECTED


def test_context_manager_lifecycle() -> None:
    scope = make_scope()
    with scope:
        assert scope.status() == InstrumentStatus.SIMULATED
    assert scope.status() == InstrumentStatus.DISCONNECTED


def test_connect_is_idempotent() -> None:
    scope = make_scope()
    scope.connect()
    scope.connect()
    assert scope.status() == InstrumentStatus.SIMULATED


# ---------------------------------------------------------------------------
# configure_channel
# ---------------------------------------------------------------------------


def test_configure_channel_does_not_raise() -> None:
    with make_scope() as scope:
        scope.configure_channel(1, volts_per_div=1.0, coupling="DC")
        scope.configure_channel(2, volts_per_div=0.5, coupling="AC")


def test_configure_channel_string_identifiers() -> None:
    with make_scope() as scope:
        scope.configure_channel("CH1", volts_per_div=2.0)
        scope.configure_channel("CH2", volts_per_div=0.2, enabled=False)


def test_configure_channel_invalid_raises() -> None:
    with make_scope() as scope:
        with pytest.raises(ValueError):
            scope.configure_channel(5, volts_per_div=1.0)


def test_configure_channel_before_connect_raises() -> None:
    scope = make_scope()
    with pytest.raises(RuntimeError, match="not connected"):
        scope.configure_channel(1, volts_per_div=1.0)


# ---------------------------------------------------------------------------
# configure_timebase
# ---------------------------------------------------------------------------


def test_configure_timebase_does_not_raise() -> None:
    with make_scope() as scope:
        scope.configure_timebase(1e-3, trigger_level_v=0.5, trigger_slope="rising")


def test_configure_timebase_falling_slope() -> None:
    with make_scope() as scope:
        scope.configure_timebase(5e-4, trigger_slope="falling")


def test_configure_timebase_before_connect_raises() -> None:
    scope = make_scope()
    with pytest.raises(RuntimeError, match="not connected"):
        scope.configure_timebase(1e-3)


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


def test_acquire_channel_1_returns_reading() -> None:
    with make_scope() as scope:
        reading = scope.acquire(1)

    assert isinstance(reading, OscilloscopeReading)
    assert reading.channel == 1
    assert len(reading.time_s) > 0
    assert len(reading.voltage_v) == len(reading.time_s)
    assert reading.sample_rate_hz > 0


def test_acquire_channel_2_returns_reading() -> None:
    with make_scope() as scope:
        reading = scope.acquire(2)

    assert isinstance(reading, OscilloscopeReading)
    assert reading.channel == 2
    assert len(reading.voltage_v) > 0


def test_acquire_string_channel() -> None:
    with make_scope() as scope:
        reading = scope.acquire("CH1")

    assert reading.channel == "CH1"


def test_acquire_metadata_contains_backend_key() -> None:
    with make_scope() as scope:
        reading = scope.acquire(1)

    assert "backend" in reading.metadata
    assert "tektronix" in reading.metadata["backend"]


def test_acquire_metadata_contains_points() -> None:
    with make_scope() as scope:
        reading = scope.acquire(1)

    assert "points" in reading.metadata
    assert reading.metadata["points"] > 0


def test_acquire_voltage_has_nonzero_values() -> None:
    with make_scope() as scope:
        reading = scope.acquire(1)

    assert max(abs(v) for v in reading.voltage_v) > 0


def test_acquire_time_is_monotonic() -> None:
    with make_scope() as scope:
        reading = scope.acquire(1)

    times = reading.time_s
    assert all(times[i] < times[i + 1] for i in range(len(times) - 1))


def test_acquire_after_configure_channel() -> None:
    with make_scope() as scope:
        scope.configure_channel(1, volts_per_div=2.0, coupling="DC")
        scope.configure_timebase(1e-4)
        reading = scope.acquire(1)

    assert len(reading.voltage_v) > 0


def test_acquire_respects_volts_per_div() -> None:
    with make_scope() as scope:
        scope.configure_channel(1, volts_per_div=5.0)
        reading_large = scope.acquire(1)

    with make_scope() as scope:
        scope.configure_channel(1, volts_per_div=0.1)
        reading_small = scope.acquire(1)

    max_large = max(abs(v) for v in reading_large.voltage_v)
    max_small = max(abs(v) for v in reading_small.voltage_v)
    assert max_large > max_small


def test_acquire_before_connect_raises() -> None:
    scope = make_scope()
    with pytest.raises(RuntimeError, match="not connected"):
        scope.acquire(1)


def test_acquire_invalid_channel_raises() -> None:
    with make_scope() as scope:
        with pytest.raises(ValueError):
            scope.acquire(9)


def test_acquire_custom_points() -> None:
    with make_scope(acquire_points=100) as scope:
        reading = scope.acquire("CH1")

    assert reading.metadata["points"] == 100


# ---------------------------------------------------------------------------
# Tektronix-specific helpers
# ---------------------------------------------------------------------------


def test_identify_returns_string() -> None:
    with make_scope() as scope:
        idn = scope.identify()

    assert isinstance(idn, str)
    assert len(idn) > 0
    assert "TEKTRONIX" in idn.upper()


def test_autoscale_does_not_raise() -> None:
    with make_scope() as scope:
        scope.autoscale()


def test_run_stop_do_not_raise() -> None:
    with make_scope() as scope:
        scope.run()
        scope.stop()


def test_force_trigger_does_not_raise() -> None:
    with make_scope() as scope:
        scope.force_trigger()


def test_send_command_does_not_raise() -> None:
    with make_scope() as scope:
        scope.send_command("ACQUIRE:STATE RUN")


def test_query_returns_string() -> None:
    with make_scope() as scope:
        result = scope.query("HORIZONTAL:SCALE?")

    assert isinstance(result, str)


def test_helpers_before_connect_raise() -> None:
    scope = make_scope()
    with pytest.raises(RuntimeError):
        scope.identify()
    with pytest.raises(RuntimeError):
        scope.autoscale()
    with pytest.raises(RuntimeError):
        scope.run()
    with pytest.raises(RuntimeError):
        scope.stop()
    with pytest.raises(RuntimeError):
        scope.force_trigger()
    with pytest.raises(RuntimeError):
        scope.send_command("*RST")
    with pytest.raises(RuntimeError):
        scope.query("*IDN?")
