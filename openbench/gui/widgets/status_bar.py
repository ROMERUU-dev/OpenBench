"""Bottom status bar widget."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


class StatusBar(ctk.CTkFrame):
    """A thin status bar displayed at the bottom of the main window.

    Shows a status message on the left and an optional info string on
    the right (e.g. active instrument count, current session name).
    """

    def __init__(self, master: ctk.CTkBaseClass, **kwargs) -> None:
        """Initialize the status bar.

        Args:
            master: Parent CustomTkinter widget.
            **kwargs: Forwarded to ``ctk.CTkFrame``.
        """
        colors = theme_manager.get_colors()
        kwargs.setdefault("height", 24)
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("fg_color", colors["bg_secondary"])
        super().__init__(master, **kwargs)
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)

        self._status_label = ctk.CTkLabel(
            self,
            text="Ready",
            anchor="w",
            font=(str(theme_manager.get_font("family_fallback")), 11),
            text_color=colors["text_muted"],
        )
        self._status_label.grid(row=0, column=0, sticky="w", padx=8, pady=2)

        self._info_label = ctk.CTkLabel(
            self,
            text="",
            anchor="e",
            font=(str(theme_manager.get_font("family_fallback")), 11),
            text_color=colors["text_muted"],
        )
        self._info_label.grid(row=0, column=1, sticky="e", padx=8, pady=2)

        theme_manager.on_theme_change(self._refresh_colors)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_status(self, message: str) -> None:
        """Update the left-side status message.

        Args:
            message: Text to display.
        """
        self._status_label.configure(text=message)
        logger.debug("Status bar: %s", message)

    def set_info(self, text: str) -> None:
        """Update the right-side info string.

        Args:
            text: Text to display (empty string hides the label).
        """
        self._info_label.configure(text=text)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _refresh_colors(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_secondary"])
        self._status_label.configure(text_color=colors["text_muted"])
        self._info_label.configure(text_color=colors["text_muted"])
