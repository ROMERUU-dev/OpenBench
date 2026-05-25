"""Theme toggle button widget."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


class ThemeToggleButton(ctk.CTkButton):
    """A button that toggles the application between dark and light themes.

    Updates its own label to reflect the current mode and delegates the
    actual appearance switch to the module-level ``theme_manager``.
    """

    def __init__(self, master: ctk.CTkBaseClass, **kwargs) -> None:
        """Initialize the toggle button.

        Args:
            master: Parent CustomTkinter widget.
            **kwargs: Forwarded to ``ctk.CTkButton``.
        """
        kwargs.setdefault("width", 90)
        kwargs.setdefault("height", 30)
        kwargs.setdefault("corner_radius", 15)
        kwargs.setdefault("text", self._label())
        super().__init__(master, command=self._on_click, **kwargs)
        theme_manager.on_theme_change(self._on_theme_changed)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _label(self) -> str:
        mode = theme_manager.get_effective_mode()
        return "☀ Light" if mode == "dark" else "☾ Dark"

    def _on_click(self) -> None:
        theme_manager.toggle()

    def _on_theme_changed(self, _mode: str) -> None:
        self.configure(text=self._label())
        logger.debug("ThemeToggleButton updated label to %s", self._label())
