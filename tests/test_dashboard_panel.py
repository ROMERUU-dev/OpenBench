"""Tests for DashboardPanel (headless — no CTk widget instantiation)."""

from __future__ import annotations

import importlib

import pytest


# ---------------------------------------------------------------------------
# Structural import tests
# ---------------------------------------------------------------------------


def test_dashboard_panel_importable() -> None:
    """DashboardPanel must be importable from its module."""
    mod = importlib.import_module("openbench.gui.panels.dashboard_panel")
    assert hasattr(mod, "DashboardPanel")


def test_dashboard_panel_subclasses_content_panel() -> None:
    from openbench.gui.panels.content_panel import ContentPanel
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert issubclass(DashboardPanel, ContentPanel)


def test_dashboard_panel_exported_from_panels_init() -> None:
    from openbench.gui import panels

    assert hasattr(panels, "DashboardPanel")


def test_instrument_state_importable() -> None:
    from openbench.gui.panels.dashboard_panel import InstrumentState

    state = InstrumentState(key="test", name="Test", kind="Scope", icon="⬡")
    assert state.status == "Disconnected"


# ---------------------------------------------------------------------------
# Registry / routing tests
# ---------------------------------------------------------------------------


def test_dashboard_in_panel_registry() -> None:
    """'dashboard' must be present in the ContentArea panel registry."""
    from openbench.gui.app import _PANEL_REGISTRY

    assert "dashboard" in _PANEL_REGISTRY


def test_instruments_overview_routes_to_dashboard() -> None:
    """'instruments_overview' sidebar key must route to the dashboard group."""
    from openbench.gui.app import _KEY_TO_GROUP

    assert _KEY_TO_GROUP.get("instruments_overview") == "dashboard"


def test_dashboard_panel_class_in_registry() -> None:
    from openbench.gui.app import _PANEL_REGISTRY
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert _PANEL_REGISTRY["dashboard"] is DashboardPanel


# ---------------------------------------------------------------------------
# DashboardPanel class API (no instantiation)
# ---------------------------------------------------------------------------


def test_dashboard_panel_has_set_instrument_status() -> None:
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert callable(getattr(DashboardPanel, "set_instrument_status", None))


def test_dashboard_panel_has_set_all_status() -> None:
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert callable(getattr(DashboardPanel, "set_all_status", None))


def test_dashboard_panel_has_on_show_and_on_hide() -> None:
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert callable(getattr(DashboardPanel, "on_show", None))
    assert callable(getattr(DashboardPanel, "on_hide", None))


def test_dashboard_panel_has_build_hook() -> None:
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert callable(getattr(DashboardPanel, "_build", None))


def test_dashboard_panel_has_refresh_theme() -> None:
    from openbench.gui.panels.dashboard_panel import DashboardPanel

    assert callable(getattr(DashboardPanel, "_refresh_theme", None))


# ---------------------------------------------------------------------------
# InstrumentState data model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["Connected", "Simulated", "Disconnected"])
def test_instrument_state_valid_statuses(status: str) -> None:
    from openbench.gui.panels.dashboard_panel import InstrumentState

    state = InstrumentState(key="vb", name="VirtualBench", kind="Osc", icon="⬡", status=status)
    assert state.status == status


def test_instrument_state_defaults_disconnected() -> None:
    from openbench.gui.panels.dashboard_panel import InstrumentState

    state = InstrumentState(key="sr860", name="SR860", kind="Lock-in", icon="⬡")
    assert state.status == "Disconnected"


# ---------------------------------------------------------------------------
# Registry completeness (cross-check with other tests)
# ---------------------------------------------------------------------------


def test_all_key_to_group_values_in_registry() -> None:
    """Every group value in _KEY_TO_GROUP must resolve to a registered panel."""
    from openbench.gui.app import _KEY_TO_GROUP, _PANEL_REGISTRY

    for key, group in _KEY_TO_GROUP.items():
        assert group in _PANEL_REGISTRY, (
            f"Key {key!r} maps to group {group!r} not in _PANEL_REGISTRY"
        )
