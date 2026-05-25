"""Tests for MainWindow ContentArea and panel navigation routing."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Navigation key routing constants
# ---------------------------------------------------------------------------


def test_key_to_group_covers_all_sidebar_keys() -> None:
    """Every sidebar item key must have an entry in _KEY_TO_GROUP."""
    from openbench.gui.app import _DEFAULT_SECTIONS, _KEY_TO_GROUP

    sidebar_keys = {item.key for section in _DEFAULT_SECTIONS for item in section.items}
    missing = sidebar_keys - set(_KEY_TO_GROUP.keys())
    assert not missing, f"Sidebar keys missing from _KEY_TO_GROUP: {missing}"


def test_key_to_group_values_are_valid_registry_keys() -> None:
    """All _KEY_TO_GROUP values must correspond to a registered panel group."""
    from openbench.gui.app import _KEY_TO_GROUP, _PANEL_REGISTRY

    for sidebar_key, group in _KEY_TO_GROUP.items():
        assert group in _PANEL_REGISTRY, (
            f"Sidebar key {sidebar_key!r} maps to group {group!r} "
            f"which is absent from _PANEL_REGISTRY"
        )


def test_panel_registry_has_welcome_fallback() -> None:
    """'welcome' must be in the registry as the default fallback panel."""
    from openbench.gui.app import _PANEL_REGISTRY

    assert "welcome" in _PANEL_REGISTRY


def test_panel_registry_all_subclass_content_panel() -> None:
    """Every value in _PANEL_REGISTRY must subclass ContentPanel."""
    from openbench.gui.app import _PANEL_REGISTRY
    from openbench.gui.panels.content_panel import ContentPanel

    for key, panel_cls in _PANEL_REGISTRY.items():
        assert issubclass(panel_cls, ContentPanel), (
            f"Panel class for {key!r} is not a ContentPanel subclass"
        )


def test_default_sections_not_empty() -> None:
    from openbench.gui.app import _DEFAULT_SECTIONS

    assert len(_DEFAULT_SECTIONS) > 0
    for section in _DEFAULT_SECTIONS:
        assert len(section.items) > 0, f"Section {section.title!r} has no items"


# ---------------------------------------------------------------------------
# ContentPanel class API (no widget instantiation)
# ---------------------------------------------------------------------------


def test_content_panel_has_lifecycle_hooks() -> None:
    from openbench.gui.panels.content_panel import ContentPanel

    assert callable(getattr(ContentPanel, "on_show", None))
    assert callable(getattr(ContentPanel, "on_hide", None))


def test_content_panel_has_build_hook() -> None:
    from openbench.gui.panels.content_panel import ContentPanel

    assert callable(getattr(ContentPanel, "_build", None))


def test_content_panel_has_refresh_theme_hook() -> None:
    from openbench.gui.panels.content_panel import ContentPanel

    assert callable(getattr(ContentPanel, "_refresh_theme", None))


# ---------------------------------------------------------------------------
# ContentArea class API (no widget instantiation)
# ---------------------------------------------------------------------------


def test_content_area_has_navigate_method() -> None:
    from openbench.gui.panels.content_area import ContentArea

    assert callable(getattr(ContentArea, "navigate", None))


def test_content_area_current_key_is_property() -> None:
    from openbench.gui.panels.content_area import ContentArea

    assert isinstance(ContentArea.__dict__.get("current_key"), property)


# ---------------------------------------------------------------------------
# Panel module imports (structural smoke tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path,class_name",
    [
        ("openbench.gui.panels.welcome_panel", "WelcomePanel"),
        ("openbench.gui.panels.instruments_panel", "InstrumentsPanel"),
        ("openbench.gui.panels.experiments_panel", "ExperimentsPanel"),
        ("openbench.gui.panels.filters_panel", "FiltersPanel"),
        ("openbench.gui.panels.data_panel", "DataPanel"),
    ],
)
def test_panel_class_importable(module_path: str, class_name: str) -> None:
    import importlib

    from openbench.gui.panels.content_panel import ContentPanel

    mod = importlib.import_module(module_path)
    panel_cls = getattr(mod, class_name)
    assert issubclass(panel_cls, ContentPanel)


# ---------------------------------------------------------------------------
# _PANEL_REGISTRY completeness
# ---------------------------------------------------------------------------


def test_panel_registry_covers_all_groups() -> None:
    """Every group referenced in _KEY_TO_GROUP must be in _PANEL_REGISTRY."""
    from openbench.gui.app import _KEY_TO_GROUP, _PANEL_REGISTRY

    groups_used = set(_KEY_TO_GROUP.values())
    missing = groups_used - set(_PANEL_REGISTRY.keys())
    assert not missing, f"Groups used in _KEY_TO_GROUP but not registered: {missing}"
