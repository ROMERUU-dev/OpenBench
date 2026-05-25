"""Tests for InstrumentOrchestrator discovery and lifecycle."""

from __future__ import annotations

import pytest

from openbench.core.interfaces import IDCSupply, IInstrument, InstrumentStatus, DCSweepReading
from openbench.core.orchestrator import InstrumentOrchestrator


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class StubInstrument(IInstrument):
    """Minimal adapter for testing orchestrator registry and lifecycle."""

    connect_calls: int
    disconnect_calls: int

    def __post_init__(self) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0

    def _connect(self) -> None:
        self.connect_calls += 1

    def _disconnect(self) -> None:
        self.disconnect_calls += 1


class StubDCSupply(IDCSupply):
    """Minimal DC supply adapter for interface-typed query tests."""

    def __post_init__(self) -> None:
        self.voltage_setpoints: list[tuple[str | int, float]] = []

    def _connect(self) -> None:
        pass

    def _disconnect(self) -> None:
        pass

    def set_voltage(self, channel: str | int, voltage_v: float) -> None:
        self.voltage_setpoints.append((channel, voltage_v))

    def set_current(self, channel: str | int, current_a: float) -> None:
        pass

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
        return []


class FailingStub(StubInstrument):
    """Adapter that raises on connect for error-path tests."""

    def _connect(self) -> None:
        raise RuntimeError("hardware fault")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_and_get_instrument() -> None:
    """Registered adapter is retrievable by name."""
    orc = InstrumentOrchestrator()
    inst = StubInstrument(name="scope")
    orc.register(inst)
    assert orc.get("scope") is inst


def test_register_duplicate_raises_value_error() -> None:
    """Registering two adapters with the same name raises ValueError."""
    orc = InstrumentOrchestrator()
    orc.register(StubInstrument(name="dup"))
    with pytest.raises(ValueError, match="already registered"):
        orc.register(StubInstrument(name="dup"))


def test_unregister_removes_instrument() -> None:
    """Unregistered adapter is no longer returned by get."""
    orc = InstrumentOrchestrator()
    inst = StubInstrument(name="gen")
    orc.register(inst)
    removed = orc.unregister("gen")
    assert removed is inst
    assert orc.get("gen") is None


def test_unregister_missing_returns_none() -> None:
    """Unregistering an unknown name returns None without raising."""
    orc = InstrumentOrchestrator()
    assert orc.unregister("ghost") is None


# ---------------------------------------------------------------------------
# Interface-typed queries
# ---------------------------------------------------------------------------


def test_get_by_interface_returns_matching_adapters() -> None:
    """get_by_interface filters by abstract interface type."""
    orc = InstrumentOrchestrator()
    supply = StubDCSupply(name="keysight", simulate=True)
    generic = StubInstrument(name="scope", simulate=True)
    orc.register(supply)
    orc.register(generic)

    dc_supplies = orc.get_by_interface(IDCSupply)
    assert dc_supplies == [supply]

    instruments = orc.get_by_interface(IInstrument)
    assert sorted(i.name for i in instruments) == ["keysight", "scope"]


def test_get_by_interface_returns_empty_list_when_none_match() -> None:
    """get_by_interface returns [] when no adapter matches the type."""
    orc = InstrumentOrchestrator()
    orc.register(StubInstrument(name="scope", simulate=True))
    assert orc.get_by_interface(IDCSupply) == []


# ---------------------------------------------------------------------------
# list_instruments
# ---------------------------------------------------------------------------


def test_list_instruments_sorted_by_name() -> None:
    """list_instruments returns (name, status) pairs in alphabetical order."""
    orc = InstrumentOrchestrator()
    orc.register(StubInstrument(name="zebra", simulate=True))
    orc.register(StubInstrument(name="alpha", simulate=True))

    listing = orc.list_instruments()
    assert [name for name, _ in listing] == ["alpha", "zebra"]
    assert all(status == InstrumentStatus.DISCONNECTED for _, status in listing)


# ---------------------------------------------------------------------------
# Simulation propagation
# ---------------------------------------------------------------------------


def test_orchestrator_simulation_mode_propagates_to_adapters() -> None:
    """Adapters registered on a simulate=True orchestrator are forced simulated."""
    orc = InstrumentOrchestrator(simulate=True)
    inst = StubInstrument(name="hw", simulate=False)
    orc.register(inst)
    assert inst.simulate is True


def test_adapter_with_simulate_true_unchanged() -> None:
    """Adapters already marked simulate=True are not affected when orchestrator
    is not in simulation mode."""
    orc = InstrumentOrchestrator(simulate=False)
    inst = StubInstrument(name="sim", simulate=True)
    orc.register(inst)
    assert inst.simulate is True


# ---------------------------------------------------------------------------
# Bulk lifecycle
# ---------------------------------------------------------------------------


def test_connect_all_connects_all_adapters() -> None:
    """connect_all connects every registered adapter and reports no errors."""
    orc = InstrumentOrchestrator()
    a = StubInstrument(name="a")
    b = StubInstrument(name="b")
    orc.register(a)
    orc.register(b)

    errors = orc.connect_all()
    assert errors == {"a": None, "b": None}
    assert a.status() == InstrumentStatus.CONNECTED
    assert b.status() == InstrumentStatus.CONNECTED


def test_connect_all_collects_partial_failures() -> None:
    """connect_all continues even when one adapter fails and records the error."""
    orc = InstrumentOrchestrator()
    good = StubInstrument(name="good")
    bad = FailingStub(name="bad")
    orc.register(good)
    orc.register(bad)

    errors = orc.connect_all()
    assert errors["good"] is None
    assert isinstance(errors["bad"], RuntimeError)
    assert good.status() == InstrumentStatus.CONNECTED


def test_disconnect_all_disconnects_connected_adapters() -> None:
    """disconnect_all cleanly disconnects previously connected adapters."""
    orc = InstrumentOrchestrator()
    inst = StubInstrument(name="inst")
    orc.register(inst)
    orc.connect_all()

    errors = orc.disconnect_all()
    assert errors == {"inst": None}
    assert inst.status() == InstrumentStatus.DISCONNECTED


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


def test_context_manager_connects_and_disconnects() -> None:
    """Using the orchestrator as a context manager connects then disconnects."""
    orc = InstrumentOrchestrator()
    inst = StubInstrument(name="ctx")
    orc.register(inst)

    with orc:
        assert inst.status() == InstrumentStatus.CONNECTED

    assert inst.status() == InstrumentStatus.DISCONNECTED


def test_context_manager_disconnects_on_exception() -> None:
    """disconnect_all is called even when the with-body raises."""
    orc = InstrumentOrchestrator()
    inst = StubInstrument(name="exc")
    orc.register(inst)

    with pytest.raises(RuntimeError):
        with orc:
            assert inst.status() == InstrumentStatus.CONNECTED
            raise RuntimeError("boom")

    assert inst.status() == InstrumentStatus.DISCONNECTED


# ---------------------------------------------------------------------------
# VISA discovery
# ---------------------------------------------------------------------------


def test_discover_visa_simulate_returns_empty_list() -> None:
    """discover_visa with simulate=True returns [] without hardware access."""
    resources = InstrumentOrchestrator.discover_visa(simulate=True)
    assert resources == []


def test_discover_visa_without_pyvisa_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover_visa returns [] and does not raise when pyvisa is absent."""
    import builtins
    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pyvisa":
            raise ImportError("pyvisa not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    resources = InstrumentOrchestrator.discover_visa(simulate=False)
    assert resources == []
