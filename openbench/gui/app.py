"""Main OpenBench application window."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.panels.header import HeaderBar
from openbench.gui.panels.sidebar import SidebarItem, SidebarPanel, SidebarSection
from openbench.gui.theme import theme_manager
from openbench.gui.widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)

_WINDOW_TITLE = "OpenBench"
_WINDOW_MIN_W = 1024
_WINDOW_MIN_H = 640
_WINDOW_DEFAULT_W = 1280
_WINDOW_DEFAULT_H = 780

_DEFAULT_SECTIONS: list[SidebarSection] = [
    SidebarSection(
        title="Instruments",
        items=[
            SidebarItem(label="Overview", key="instruments_overview", icon="◈"),
            SidebarItem(label="VirtualBench", key="instruments_vb", icon="⬡"),
            SidebarItem(label="SR860 Lock-in", key="instruments_sr860", icon="⬡"),
            SidebarItem(label="Keysight DC", key="instruments_keysight", icon="⬡"),
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
    - **Content area**: swappable panel shown on the right.

    The window subscribes to ``theme_manager`` so the background refreshes
    automatically on theme switch without requiring a restart.
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
        self.grid_rowconfigure(0, weight=0)  # header
        self.grid_rowconfigure(1, weight=1)  # body
        self.grid_rowconfigure(2, weight=0)  # status bar
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

        # Content placeholder
        colors = theme_manager.get_colors()
        self._content = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=colors["bg_primary"],
        )
        self._content.grid(row=1, column=1, sticky="nsew")
        self._content.columnconfigure(0, weight=1)
        self._content.rowconfigure(0, weight=1)

        self._welcome_label = ctk.CTkLabel(
            self._content,
            text="Select an item from the sidebar to get started.",
            font=(str(theme_manager.get_font("family_fallback")), 16),
            text_color=theme_manager.get_color("text_muted"),
        )
        self._welcome_label.grid(row=0, column=0)

        # Status bar
        self._status_bar = StatusBar(self)
        self._status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_navigate(self, key: str) -> None:
        logger.debug("Navigation event: %s", key)
        self._status_bar.set_status(f"Section: {key.replace('_', ' ').title()}")

    def _on_connect_clicked(self) -> None:
        logger.info("Connect button clicked")
        self._status_bar.set_status("Scanning for instruments…")

    def _on_theme_changed(self, mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        self._content.configure(fg_color=colors["bg_primary"])
        self._welcome_label.configure(text_color=colors["text_muted"])
        logger.debug("App root background refreshed for mode: %s", mode)
