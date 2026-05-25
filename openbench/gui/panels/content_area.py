"""Frame widget that hosts and switches between ContentPanel instances."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


class ContentArea(ctk.CTkFrame):
    """Manages a registry of ContentPanel subclasses and switches between them.

    Panels are lazily instantiated on first navigation and cached so that
    widget state survives between visits. Switching is implemented via
    ``grid`` show/hide rather than widget destruction, which avoids re-
    building potentially expensive layouts on every navigation event.

    The key ``"welcome"`` in *panel_registry* is used as the fallback when
    :meth:`navigate` receives an unknown key.

    Args:
        master: Parent CustomTkinter widget.
        panel_registry: Mapping from navigation group key to a
            ``ContentPanel`` subclass. Must include ``"welcome"``.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        panel_registry: dict[str, type[ContentPanel]],
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("fg_color", colors["bg_primary"])
        super().__init__(master, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._registry: dict[str, type[ContentPanel]] = dict(panel_registry)
        self._cache: dict[str, ContentPanel] = {}
        self._current_key: str | None = None

        theme_manager.on_theme_change(self._refresh_theme)
        logger.debug("ContentArea ready with %d panel types", len(self._registry))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def navigate(self, key: str) -> None:
        """Show the panel registered under *key*.

        Falls back to ``"welcome"`` when *key* is not in the registry.
        A no-op if the requested panel is already visible.

        Args:
            key: Navigation group key corresponding to a registered panel.
        """
        resolved = key if key in self._registry else "welcome"
        if resolved not in self._registry:
            logger.warning("ContentArea.navigate(%r): no panel and no 'welcome' fallback", key)
            return
        if resolved == self._current_key:
            return

        self._hide_current()
        panel = self._get_or_create(resolved)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.on_show()
        self._current_key = resolved
        logger.debug("ContentArea → %r", resolved)

    @property
    def current_key(self) -> str | None:
        """The navigation key of the currently displayed panel, or ``None``."""
        return self._current_key

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _hide_current(self) -> None:
        if self._current_key is None:
            return
        panel = self._cache.get(self._current_key)
        if panel is not None:
            panel.on_hide()
            panel.grid_remove()

    def _get_or_create(self, key: str) -> ContentPanel:
        if key not in self._cache:
            panel_cls = self._registry[key]
            self._cache[key] = panel_cls(self)
            logger.debug("Instantiated %s for key=%r", panel_cls.__name__, key)
        return self._cache[key]

    def _refresh_theme(self, _mode: str) -> None:
        self.configure(fg_color=theme_manager.get_color("bg_primary"))
