"""Abstract base class for swappable MainWindow content panels."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


class ContentPanel(ctk.CTkFrame):
    """Base class for all MainWindow content panels.

    Subclass and override ``_build`` to populate widgets. Override
    ``on_show`` / ``on_hide`` to react to visibility transitions managed
    by :class:`ContentArea`.

    Args:
        master: Parent CustomTkinter widget.
        **kwargs: Forwarded to ``ctk.CTkFrame``.
    """

    def __init__(self, master: ctk.CTkBaseClass, **kwargs) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("fg_color", colors["bg_primary"])
        super().__init__(master, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._build()
        theme_manager.on_theme_change(self._refresh_theme)
        logger.debug("Initialized %s", self.__class__.__name__)

    # ------------------------------------------------------------------
    # Lifecycle hooks – override in subclasses
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        """Called when this panel becomes the active content view."""
        logger.debug("%s.on_show()", self.__class__.__name__)

    def on_hide(self) -> None:
        """Called just before this panel is replaced by another."""
        logger.debug("%s.on_hide()", self.__class__.__name__)

    # ------------------------------------------------------------------
    # Subclass helpers
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Override to create child widgets. Called once during ``__init__``."""

    def _refresh_theme(self, _mode: str) -> None:
        """Override to update widget colors when the effective theme changes."""
        self.configure(fg_color=theme_manager.get_color("bg_primary"))
