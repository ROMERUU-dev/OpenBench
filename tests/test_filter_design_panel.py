"""Tests for FiltersPanel (SOFIA integrated GUI panel)."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Class structure
# ---------------------------------------------------------------------------


def test_filters_panel_importable() -> None:
    """FiltersPanel must be importable and subclass ContentPanel."""
    from openbench.gui.panels.content_panel import ContentPanel
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert issubclass(FiltersPanel, ContentPanel)


def test_filters_panel_has_on_design() -> None:
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert callable(getattr(FiltersPanel, "_on_design", None))


def test_filters_panel_has_on_design_done() -> None:
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert callable(getattr(FiltersPanel, "_on_design_done", None))


def test_filters_panel_has_on_design_error() -> None:
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert callable(getattr(FiltersPanel, "_on_design_error", None))


def test_filters_panel_has_update_bode_plot() -> None:
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert callable(getattr(FiltersPanel, "_update_bode_plot", None))


def test_filters_panel_has_copy_netlist() -> None:
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert callable(getattr(FiltersPanel, "_on_copy_netlist", None))


def test_filters_panel_has_set_status() -> None:
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert callable(getattr(FiltersPanel, "_set_status", None))


def test_filters_panel_in_panel_registry() -> None:
    """FiltersPanel must be registered under the 'filters' group key."""
    from openbench.gui.app import _PANEL_REGISTRY
    from openbench.gui.panels.filters_panel import FiltersPanel

    assert _PANEL_REGISTRY.get("filters") is FiltersPanel


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_sofia_available_flag_is_bool() -> None:
    from openbench.gui.panels.filters_panel import _SOFIA_AVAILABLE

    assert isinstance(_SOFIA_AVAILABLE, bool)


def test_kind_labels_cover_lp_hp() -> None:
    from openbench.gui.panels.filters_panel import _KIND_LABELS

    assert "Low-Pass" in _KIND_LABELS
    assert "High-Pass" in _KIND_LABELS


def test_approx_labels_include_butterworth() -> None:
    from openbench.gui.panels.filters_panel import _APPROX_LABELS

    assert "Butterworth" in _APPROX_LABELS
    assert len(_APPROX_LABELS) >= 2


def test_topology_labels_include_sallen_key() -> None:
    from openbench.gui.panels.filters_panel import _TOPOLOGY_LABELS

    assert "Sallen-Key" in _TOPOLOGY_LABELS


# ---------------------------------------------------------------------------
# _parse_float helper
# ---------------------------------------------------------------------------


def test_parse_float_integer_string() -> None:
    from openbench.gui.panels.filters_panel import _parse_float

    assert _parse_float("1000", "passband") == 1000.0


def test_parse_float_decimal_string() -> None:
    from openbench.gui.panels.filters_panel import _parse_float

    assert abs(_parse_float("0.707", "Q") - 0.707) < 1e-10


def test_parse_float_scientific_notation() -> None:
    from openbench.gui.panels.filters_panel import _parse_float

    assert _parse_float("1e3", "freq") == 1000.0


def test_parse_float_invalid_raises_value_error() -> None:
    from openbench.gui.panels.filters_panel import _parse_float

    with pytest.raises(ValueError, match="passband"):
        _parse_float("abc", "passband")


def test_parse_float_empty_string_raises() -> None:
    from openbench.gui.panels.filters_panel import _parse_float

    with pytest.raises(ValueError, match="freq"):
        _parse_float("", "freq")


# ---------------------------------------------------------------------------
# _format_stage helper
# ---------------------------------------------------------------------------


def test_format_stage_no_attributes_returns_string() -> None:
    from openbench.gui.panels.filters_panel import _format_stage

    class _Bare:
        pass

    result = _format_stage(_Bare())
    assert isinstance(result, str)


def test_format_stage_with_q_factor() -> None:
    from openbench.gui.panels.filters_panel import _format_stage

    class _Stage:
        q_factor = 0.707

    result = _format_stage(_Stage())
    assert "q_factor" in result
    assert "0.707" in result


def test_format_stage_with_multiple_attributes() -> None:
    from openbench.gui.panels.filters_panel import _format_stage

    class _Stage:
        q_factor = 1.0
        fc_hz = 1000.0

    result = _format_stage(_Stage())
    assert "q_factor" in result
    assert "fc_hz" in result
