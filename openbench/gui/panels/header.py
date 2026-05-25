"""Top header bar panel."""

from __future__ import annotations

import logging
from typing import Callable

import customtkinter as ctk

from openbench.gui.theme import theme_manager
from openbench.gui.widgets.theme_toggle import ThemeToggleButton

logger = logging.getLogger(__name__)


class HeaderBar(ctk.CTkFrame):
    """Top navigation bar for the OpenBench main window.

    Displays the application title on the left, an optional subtitle, and
    a theme-toggle button plus action buttons on the right.

    Args:
        master: Parent CustomTkinter widget.
        title: Main title text.
        subtitle: Secondary text shown below the title.
        on_connect: Callback fired when the "Connect" button is clicked.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        title: str = "OpenBench",
        subtitle: str = "Lab Instrument Orchestration",
        on_connect: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("height", 64)
        kwargs.setdefault("fg_color", colors["bg_secondary"])
        super().__init__(master, **kwargs)

        self.grid_propagate(False)
        self.columnconfigure(0, weight=0)  # logo area
        self.columnconfigure(1, weight=1)  # spacer
        self.columnconfigure(2, weight=0)  # actions area

        # --- Logo / title block ---
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="w", padx=(16, 0), pady=8)

        self._title_label = ctk.CTkLabel(
            title_frame,
            text=title,
            font=(str(theme_manager.get_font("family_fallback")), 18, "bold"),
            text_color=colors["accent_primary"],
            anchor="w",
        )
        self._title_label.pack(side="top", anchor="w")

        self._subtitle_label = ctk.CTkLabel(
            title_frame,
            text=subtitle,
            font=(str(theme_manager.get_font("family_fallback")), 11),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._subtitle_label.pack(side="top", anchor="w")

        # --- Action buttons ---
        actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        actions_frame.grid(row=0, column=2, sticky="e", padx=(0, 16), pady=8)

        self._theme_btn = ThemeToggleButton(actions_frame)
        self._theme_btn.pack(side="left", padx=(0, 8))

        if on_connect is not None:
            self._connect_btn = ctk.CTkButton(
                actions_frame,
                text="Connect",
                width=90,
                height=30,
                corner_radius=15,
                command=on_connect,
                fg_color=colors["accent_primary"],
                hover_color=colors["accent_hover"],
                text_color=colors["text_on_accent"],
            )
            self._connect_btn.pack(side="left", padx=(0, 4))

        theme_manager.on_theme_change(self._refresh_colors)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _refresh_colors(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_secondary"])
        self._title_label.configure(text_color=colors["accent_primary"])
        self._subtitle_label.configure(text_color=colors["text_muted"])
