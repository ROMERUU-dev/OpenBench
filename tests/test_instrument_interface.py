"""Tests for the base instrument lifecycle."""

from __future__ import annotations

import pytest

from openbench.core.interfaces import DCSweepReading, IDCSupply, IInstrument, InstrumentStatus


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


class DummyDCSupply(IDCSupply):
    """Minimal DC supply adapter used to verify the abstract interface."""

    def __post_init__(self) -> None:
        """Initialize captured setpoints for assertions."""

        self.voltage_setpoints: list[tuple[str | int, float]] = []
        self.current_limits: list[tuple[str | int, float]] = []

    def _connect(self) -> None:
        """Open no resources for the test adapter."""

    def _disconnect(self) -> None:
        """Close no resources for the test adapter."""

    def set_voltage(self, channel: str | int, voltage_v: float) -> None:
        """Record a voltage setpoint."""

        self.voltage_setpoints.append((channel, voltage_v))

    def set_current(self, channel: str | int, current_a: float) -> None:
        """Record a current compliance limit."""

        self.current_limits.append((channel, current_a))

    def sweep(
        self,
        channel: str | int,
        start_v: float,
        stop_v: float,
        step_v: float,
        *,
        current_limit_a: float | None = None,
        dwell_s: float = 0.0,
    ) -> list[DCSweepReading]:
        """Return deterministic readings for the requested sweep."""

        if step_v == 0:
            raise ValueError("step_v must be non-zero")

        return [
            DCSweepReading(
                channel=channel,
                voltage_setpoint_v=start_v,
                current_limit_a=current_limit_a,
                measured_voltage_v=start_v,
                measured_current_a=0.0,
                metadata={"dwell_s": dwell_s},
            )
        ]


def test_dc_supply_interface_contract_accepts_backend_adapter() -> None:
    """Verify DC supply adapters expose setpoint and sweep operations."""

    supply = DummyDCSupply(name="dc", simulate=True)

    supply.set_voltage("CH1", 1.25)
    supply.set_current("CH1", 0.01)
    readings = supply.sweep("CH1", 0.0, 1.0, 0.5, current_limit_a=0.01, dwell_s=0.1)

    assert supply.voltage_setpoints == [("CH1", 1.25)]
    assert supply.current_limits == [("CH1", 0.01)]
    assert readings == [
        DCSweepReading(
            channel="CH1",
            voltage_setpoint_v=0.0,
            current_limit_a=0.01,
            measured_voltage_v=0.0,
            measured_current_a=0.0,
            metadata={"dwell_s": 0.1},
        )
    ]
