"""CustomTkinter panels for OpenBench workflows."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from openbench.gui.panels.content_area import ContentArea
    from openbench.gui.panels.content_panel import ContentPanel
    from openbench.gui.panels.dashboard_panel import DashboardPanel
    from openbench.gui.panels.data_panel import DataPanel
    from openbench.gui.panels.experiments_panel import ExperimentsPanel
    from openbench.gui.panels.filters_panel import FiltersPanel
    from openbench.gui.panels.header import HeaderBar
    from openbench.gui.panels.instrument_setup_panel import InstrumentSetupPanel
    from openbench.gui.panels.instruments_panel import InstrumentsPanel
    from openbench.gui.panels.live_plot_panel import LivePlotPanel
    from openbench.gui.panels.session_history_panel import SessionHistoryPanel
    from openbench.gui.panels.sidebar import SidebarPanel
    from openbench.gui.panels.welcome_panel import WelcomePanel

    __all__ = [
        "ContentArea",
        "ContentPanel",
        "DashboardPanel",
        "DataPanel",
        "ExperimentsPanel",
        "FiltersPanel",
        "HeaderBar",
        "InstrumentSetupPanel",
        "InstrumentsPanel",
        "LivePlotPanel",
        "SessionHistoryPanel",
        "SidebarPanel",
        "WelcomePanel",
        "logger",
    ]
except ImportError:
    logger.debug("GUI panels unavailable (customtkinter not installed)")
    __all__ = ["logger"]
