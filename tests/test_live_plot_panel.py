"""Tests for the LivePlotPanel live plotting contract."""

from __future__ import annotations

import importlib
import math

import pytest


def test_live_plot_panel_importable() -> None:
    """LivePlotPanel must be importable from its module."""

    mod = importlib.import_module("openbench.gui.panels.live_plot_panel")
    assert hasattr(mod, "LivePlotPanel")
    assert hasattr(mod, "LivePlotBuffer")


def test_live_plot_panel_subclasses_content_panel() -> None:
    """LivePlotPanel participates in the same content-panel contract."""

    from openbench.gui.panels.content_panel import ContentPanel
    from openbench.gui.panels.live_plot_panel import LivePlotPanel

    assert issubclass(LivePlotPanel, ContentPanel)


def test_live_plot_panel_exported_from_panels_init() -> None:
    """The panels package exports LivePlotPanel."""

    from openbench.gui import panels

    assert hasattr(panels, "LivePlotPanel")


def test_live_plot_panel_registered_for_data_plots() -> None:
    """The Data > Plots navigation key opens the live plot panel."""

    from openbench.gui.app import _KEY_TO_GROUP, _PANEL_REGISTRY
    from openbench.gui.panels.live_plot_panel import LivePlotPanel

    assert _KEY_TO_GROUP["data_plots"] == "live_plot"
    assert _PANEL_REGISTRY["live_plot"] is LivePlotPanel


def test_live_plot_panel_public_api_present() -> None:
    """Backend adapters can push samples through stable public methods."""

    from openbench.gui.panels.live_plot_panel import LivePlotPanel

    public_methods = [
        "append_point",
        "add_point",
        "append_sample",
        "extend_points",
        "set_series_data",
        "clear",
        "start_animation",
        "stop_animation",
        "resume",
        "pause",
        "is_running",
        "set_autoscale",
    ]

    for method_name in public_methods:
        assert callable(getattr(LivePlotPanel, method_name, None))


def test_live_plot_buffer_keeps_bounded_series() -> None:
    """LivePlotBuffer retains only the newest max_points per series."""

    from openbench.gui.panels.live_plot_panel import LivePlotBuffer

    buffer = LivePlotBuffer(max_points=3)
    appended = buffer.extend("voltage", [(0.0, 0.0), (1.0, 1.0), (2.0, 4.0), (3.0, 9.0)])

    assert appended == 4
    assert buffer.series_keys() == ("voltage",)
    assert buffer.points("voltage") == ((1.0, 1.0), (2.0, 4.0), (3.0, 9.0))


def test_live_plot_buffer_appends_mapping_samples() -> None:
    """Mapping samples support existing experiment payload field names."""

    from openbench.gui.panels.live_plot_panel import LivePlotBuffer

    buffer = LivePlotBuffer()
    appended = buffer.append_mapping(
        {"time_s": "0.5", "voltage_v": "1.25"},
        x_field="time_s",
        y_field="voltage_v",
        series="scope",
    )

    assert appended is True
    assert buffer.points("scope") == ((0.5, 1.25),)


def test_live_plot_buffer_rejects_non_finite_values() -> None:
    """Invalid numeric samples are rejected before they reach Matplotlib."""

    from openbench.gui.panels.live_plot_panel import LivePlotBuffer

    buffer = LivePlotBuffer()
    with pytest.raises(ValueError):
        buffer.append("bad", math.nan, 1.0)

    assert buffer.append_mapping({"x": 1.0, "y": math.inf}) is False
    assert buffer.series_keys() == ()
