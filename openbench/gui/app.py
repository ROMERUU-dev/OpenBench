"""Main OpenBench application window."""

from __future__ import annotations

import logging

import customtkinter as ctk

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
from openbench.gui.panels.sidebar import SidebarItem, SidebarPanel, SidebarSection
from openbench.gui.panels.welcome_panel import WelcomePanel
from openbench.gui.theme import theme_manager
from openbench.gui.widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)

_WINDOW_TITLE = "OpenBench"
_WINDOW_MIN_W = 1024
_WINDOW_MIN_H = 640
_WINDOW_DEFAULT_W = 1280
_WINDOW_DEFAULT_H = 780

# Registry mapping ContentArea group keys to panel classes.
_PANEL_REGISTRY: dict[str, type[ContentPanel]] = {
    "welcome": WelcomePanel,
    "dashboard": DashboardPanel,
    "instrument_setup": InstrumentSetupPanel,
    "instruments": InstrumentsPanel,
    "experiments": ExperimentsPanel,
    "filters": FiltersPanel,
    "data": DataPanel,
    "session_history": SessionHistoryPanel,
    "live_plot": LivePlotPanel,
}

# Maps each sidebar item key to a ContentArea group key.
_KEY_TO_GROUP: dict[str, str] = {
    "instruments_overview": "dashboard",
    "instruments_setup": "instrument_setup",
    "instruments_vb": "instrument_setup",
    "instruments_sr860": "instrument_setup",
    "instruments_keysight": "instrument_setup",
    "instruments_rigol": "instrument_setup",
    "instruments_tektronix": "instrument_setup",
    "exp_dc_sweep": "experiments",
    "exp_freq_sweep": "experiments",
    "exp_imp_sweep": "experiments",
    "exp_chua": "experiments",
    "exp_comp_char": "experiments",
    "filter_design": "filters",
    "filter_validation": "filters",
    "data_sessions": "session_history",
    "data_plots": "live_plot",
}

_DEFAULT_SECTIONS: list[SidebarSection] = [
    SidebarSection(
        title="Instruments",
        items=[
            SidebarItem(label="Overview", key="instruments_overview", icon="◈"),
            SidebarItem(label="Setup Wizard", key="instruments_setup", icon="◇"),
            SidebarItem(label="VirtualBench", key="instruments_vb", icon="⬡"),
            SidebarItem(label="SR860 Lock-in", key="instruments_sr860", icon="⬡"),
            SidebarItem(label="Keysight DC", key="instruments_keysight", icon="⬡"),
            SidebarItem(label="Rigol Scope", key="instruments_rigol", icon="⬡"),
            SidebarItem(label="Tektronix Scope", key="instruments_tektronix", icon="⬡"),
        ],
    ),
    SidebarSection(
        title="Experiments",
        items=[
            SidebarItem(label="DC Sweep", key="exp_dc_sweep", icon="▶"),
            SidebarItem(label="Frequency Sweep", key="exp_freq_sweep", icon="▶"),
            SidebarItem(label="Impedance Sweep", key="exp_imp_sweep", icon="▶"),
            SidebarItem(label="Chua Admittance", key="exp_chua", icon="▶"),
            SidebarItem(label="Component Char.", key="exp_comp_char", icon="▶"),
        ],
    ),
    SidebarSection(
        title="Filters (SOFIA)",
        items=[
            SidebarItem(label="Filter Design", key="filter_design", icon="◇"),
            SidebarItem(label="Validation", key="filter_validation", icon="◇"),
        ],
    ),
    SidebarSection(
        title="Data",
        items=[
            SidebarItem(label="Sessions", key="data_sessions", icon="☰"),
            SidebarItem(label="Plots", key="data_plots", icon="☰"),
        ],
    ),
]


class OpenBenchApp(ctk.CTk):
    """Root application window for OpenBench.

    Builds a three-region layout:

    - **Header bar**: title, subtitle, theme toggle, connect button.
    - **Sidebar**: collapsible navigation with instrument/experiment sections.
    - **Content area**: swappable panel driven by sidebar navigation.

    Sidebar keys are translated via ``_KEY_TO_GROUP`` to a panel group key
    which ``ContentArea`` uses to select and display the appropriate
    ``ContentPanel`` subclass.
    """

    def __init__(self) -> None:
        super().__init__()
        self._setup_window()
        self._build_layout()
        theme_manager.on_theme_change(self._on_theme_changed)
        logger.info("OpenBenchApp window initialized")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        colors = theme_manager.get_colors()
        self.title(_WINDOW_TITLE)
        self.geometry(f"{_WINDOW_DEFAULT_W}x{_WINDOW_DEFAULT_H}")
        self.minsize(_WINDOW_MIN_W, _WINDOW_MIN_H)
        self.configure(fg_color=colors["bg_primary"])
        self.grid_rowconfigure(0, weight=0)   # header
        self.grid_rowconfigure(1, weight=1)   # body
        self.grid_rowconfigure(2, weight=0)   # status bar
        self.grid_columnconfigure(0, weight=0)  # sidebar
        self.grid_columnconfigure(1, weight=1)  # content

    def _build_layout(self) -> None:
        # Header
        self._header = HeaderBar(
            self,
            title=_WINDOW_TITLE,
            subtitle="Lab Instrument Orchestration",
            on_connect=self._on_connect_clicked,
        )
        self._header.grid(row=0, column=0, columnspan=2, sticky="ew")

        # Sidebar
        self._sidebar = SidebarPanel(
            self,
            sections=_DEFAULT_SECTIONS,
            on_navigate=self._on_navigate,
        )
        self._sidebar.grid(row=1, column=0, sticky="ns")

        # Content area with panel registry
        self._content_area = ContentArea(self, panel_registry=_PANEL_REGISTRY)
        self._content_area.grid(row=1, column=1, sticky="nsew")
        self._content_area.navigate("welcome")

        # Status bar
        self._status_bar = StatusBar(self)
        self._status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_navigate(self, key: str) -> None:
        group = _KEY_TO_GROUP.get(key, "welcome")
        self._content_area.navigate(group)
        self._status_bar.set_status(f"Section: {key.replace('_', ' ').title()}")
        logger.debug("Navigation: key=%s → group=%s", key, group)

    def _on_connect_clicked(self) -> None:
        logger.info("Connect button clicked")
        self._status_bar.set_status("Opening instrument setup…")
        self._content_area.navigate("instrument_setup")

    def _on_theme_changed(self, mode: str) -> None:
        self.configure(fg_color=theme_manager.get_color("bg_primary"))
        logger.debug("App root refreshed for mode: %s", mode)
