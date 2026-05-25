"""Tests for InstrumentSetupPanel and its headless connection wizard."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from openbench.core.interfaces import IInstrument, InstrumentStatus


@dataclass
class DummySetupInstrument(IInstrument):
    """Minimal instrument adapter used by wizard model tests."""

    connect_calls: int = field(default=0, init=False)
    disconnect_calls: int = field(default=0, init=False)

    def _connect(self) -> None:
        """Record a hardware connection attempt."""

        self.connect_calls += 1

    def _disconnect(self) -> None:
        """Record a hardware disconnection attempt."""

        self.disconnect_calls += 1


def _dummy_spec():
    from openbench.gui.panels.instrument_setup_panel import InstrumentSetupSpec

    return InstrumentSetupSpec(
        key="dummy",
        display_name="Dummy",
        role="Test Instrument",
        backend_cls=DummySetupInstrument,
        default_name="dummy",
        transport="visa",
        resource_hint="dummy resource",
    )


def test_instrument_setup_panel_importable() -> None:
    """InstrumentSetupPanel must be importable from its module."""

    from openbench.gui.panels.instrument_setup_panel import InstrumentSetupPanel

    assert InstrumentSetupPanel.__name__ == "InstrumentSetupPanel"


def test_instrument_setup_panel_subclasses_content_panel() -> None:
    """InstrumentSetupPanel must integrate with the ContentArea panel contract."""

    from openbench.gui.panels.content_panel import ContentPanel
    from openbench.gui.panels.instrument_setup_panel import InstrumentSetupPanel

    assert issubclass(InstrumentSetupPanel, ContentPanel)


def test_instrument_setup_panel_exported_from_panels_init() -> None:
    """The panels namespace should export InstrumentSetupPanel."""

    from openbench.gui import panels

    assert hasattr(panels, "InstrumentSetupPanel")


def test_instrument_setup_panel_registered_in_app() -> None:
    """The main app registry must expose the setup wizard panel."""

    from openbench.gui.app import _KEY_TO_GROUP, _PANEL_REGISTRY
    from openbench.gui.panels.instrument_setup_panel import InstrumentSetupPanel

    assert _PANEL_REGISTRY["instrument_setup"] is InstrumentSetupPanel
    assert _KEY_TO_GROUP["instruments_setup"] == "instrument_setup"
    assert _KEY_TO_GROUP["instruments_vb"] == "instrument_setup"


def test_available_specs_include_required_backends() -> None:
    """The default wizard should cover every backend family in CLAUDE.md."""

    from openbench.gui.panels.instrument_setup_panel import available_instrument_specs

    keys = {spec.key for spec in available_instrument_specs()}

    assert {
        "virtualbench_scope",
        "virtualbench_fgen",
        "virtualbench_supply",
        "sr860",
        "keysight",
        "rigol",
        "tektronix",
    } <= keys


def test_wizard_builds_instrument_from_config() -> None:
    """The wizard should build concrete adapters without touching hardware."""

    from openbench.gui.panels.instrument_setup_panel import (
        InstrumentConnectionConfig,
        InstrumentConnectionWizard,
    )

    wizard = InstrumentConnectionWizard(specs=[_dummy_spec()])
    config = InstrumentConnectionConfig(
        instrument_key="dummy",
        mode="hardware",
        resource="USB0::TEST::INSTR",
        name="dummy-custom",
    )

    instrument = wizard.build_instrument(config)

    assert isinstance(instrument, DummySetupInstrument)
    assert instrument.name == "dummy-custom"
    assert instrument.resource == "USB0::TEST::INSTR"
    assert instrument.simulate is False


def test_wizard_connect_selected_registers_simulated_instrument() -> None:
    """Simulation mode should connect and register without backend hooks."""

    from openbench.gui.panels.instrument_setup_panel import InstrumentConnectionWizard

    wizard = InstrumentConnectionWizard(specs=[_dummy_spec()])
    wizard.set_mode("simulate")

    result = wizard.connect_selected()
    instrument = wizard.orchestrator.get("dummy")

    assert result.ok is True
    assert result.status is InstrumentStatus.SIMULATED
    assert isinstance(instrument, DummySetupInstrument)
    assert instrument.status() is InstrumentStatus.SIMULATED
    assert instrument.connect_calls == 0


def test_wizard_disconnect_selected_unregisters_instrument() -> None:
    """Disconnect should remove the adapter from the orchestrator registry."""

    from openbench.gui.panels.instrument_setup_panel import InstrumentConnectionWizard

    wizard = InstrumentConnectionWizard(specs=[_dummy_spec()])
    wizard.connect_selected()

    result = wizard.disconnect_selected()

    assert result.ok is True
    assert result.status is InstrumentStatus.DISCONNECTED
    assert wizard.orchestrator.get("dummy") is None


def test_wizard_rejects_unknown_mode() -> None:
    """Unsupported modes should fail before an adapter is built."""

    from openbench.gui.panels.instrument_setup_panel import InstrumentConnectionWizard

    wizard = InstrumentConnectionWizard(specs=[_dummy_spec()])

    with pytest.raises(ValueError, match="Unsupported connection mode"):
        wizard.set_mode("invalid")  # type: ignore[arg-type]


def test_wizard_rejects_unknown_spec_key() -> None:
    """Unknown spec keys should be rejected explicitly."""

    from openbench.gui.panels.instrument_setup_panel import InstrumentConnectionWizard

    wizard = InstrumentConnectionWizard(specs=[_dummy_spec()])

    with pytest.raises(KeyError):
        wizard.select_instrument("missing")
