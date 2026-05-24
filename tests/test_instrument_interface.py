"""Tests for the base instrument lifecycle."""

from __future__ import annotations

import pytest

from openbench.core.interfaces import IInstrument, InstrumentStatus


class DummyInstrument(IInstrument):
    """Minimal instrument adapter used to verify lifecycle behavior."""

    connect_calls: int
    disconnect_calls: int

    def __post_init__(self) -> None:
        """Initialize call counters for backend lifecycle hooks."""

        self.connect_calls = 0
        self.disconnect_calls = 0

    def _connect(self) -> None:
        """Record a backend connection attempt."""

        self.connect_calls += 1

    def _disconnect(self) -> None:
        """Record a backend disconnection attempt."""

        self.disconnect_calls += 1


class FailingInstrument(DummyInstrument):
    """Instrument adapter that fails while connecting."""

    def _connect(self) -> None:
        """Raise a deterministic connection failure."""

        raise RuntimeError("connection failed")


def test_instrument_connection_lifecycle_is_idempotent() -> None:
    """Verify connect and disconnect manage status without duplicate hooks."""

    instrument = DummyInstrument(name="dummy", resource="SIM::DUMMY")

    assert instrument.status() == InstrumentStatus.DISCONNECTED
    instrument.connect()
    instrument.connect()

    assert instrument.status() == InstrumentStatus.CONNECTED
    assert instrument.connect_calls == 1

    instrument.disconnect()
    instrument.disconnect()

    assert instrument.status() == InstrumentStatus.DISCONNECTED
    assert instrument.disconnect_calls == 1


def test_simulated_instrument_skips_backend_hooks() -> None:
    """Verify simulation mode reports simulated state without hardware access."""

    instrument = DummyInstrument(name="simulated", simulate=True)

    instrument.connect()
    assert instrument.status() == InstrumentStatus.SIMULATED
    assert instrument.connect_calls == 0

    instrument.disconnect()
    assert instrument.status() == InstrumentStatus.DISCONNECTED
    assert instrument.disconnect_calls == 0


def test_connection_failure_records_error_state() -> None:
    """Verify backend connection failures are recorded and re-raised."""

    instrument = FailingInstrument(name="failing")

    with pytest.raises(RuntimeError, match="connection failed"):
        instrument.connect()

    assert instrument.status() == InstrumentStatus.ERROR
    assert isinstance(instrument.last_error(), RuntimeError)
