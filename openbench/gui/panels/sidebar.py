"""Navigation sidebar panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import customtkinter as ctk

from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


@dataclass
class SidebarItem:
    """A single navigation entry in the sidebar.

    Attributes:
        label: Display text.
        icon: Optional single-character icon or emoji prefix.
        key: Unique string key used to identify the selected item.
        callback: Invoked with the item key when the entry is clicked.
    """

    label: str
    key: str
    icon: str = ""
    callback: Callable[[str], None] | None = None


@dataclass
class SidebarSection:
    """A titled group of sidebar items.

    Attributes:
        title: Section heading text (shown as a muted label).
        items: Ordered list of navigation items in this section.
    """

    title: str
    items: list[SidebarItem] = field(default_factory=list)


class SidebarPanel(ctk.CTkScrollableFrame):
    """Left navigation sidebar with section groupings.

    Renders titled sections each containing clickable navigation buttons.
    Highlights the currently active item. Subscribes to theme changes to
    refresh button colors automatically.

    Args:
        master: Parent CustomTkinter widget.
        sections: Ordered list of ``SidebarSection`` objects to render.
        on_navigate: Called with the selected item key when the user
            clicks any navigation button.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        sections: list[SidebarSection] | None = None,
        on_navigate: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("width", 220)
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("fg_color", colors["sidebar_bg"])
        kwargs.setdefault("label_fg_color", colors["sidebar_bg"])
        kwargs.setdefault("label_text", "")
        super().__init__(master, **kwargs)

        self._on_navigate = on_navigate
        self._active_key: str | None = None
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._sections = sections or []

        self._build(self._sections)
        theme_manager.on_theme_change(self._refresh_colors)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_active(self, key: str) -> None:
        """Highlight the navigation button matching *key*.

        Args:
            key: The item key to mark as active.
        """
        colors = theme_manager.get_colors()
        if self._active_key and self._active_key in self._buttons:
            self._buttons[self._active_key].configure(
                fg_color="transparent",
                text_color=colors["text_secondary"],
            )
        self._active_key = key
        if key in self._buttons:
            self._buttons[key].configure(
                fg_color=colors["sidebar_active"],
                text_color=colors["text_primary"],
            )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build(self, sections: list[SidebarSection]) -> None:
        colors = theme_manager.get_colors()
        font_sm = (str(theme_manager.get_font("family_fallback")), 10)
        font_base = (str(theme_manager.get_font("family_fallback")), 13)

        for section in sections:
            section_label = ctk.CTkLabel(
                self,
                text=section.title.upper(),
                font=font_sm,
                text_color=colors["text_muted"],
                anchor="w",
            )
            section_label.pack(fill="x", padx=16, pady=(12, 2))

            for item in section.items:
                display = f"  {item.icon}  {item.label}" if item.icon else f"  {item.label}"
                btn = ctk.CTkButton(
                    self,
                    text=display,
                    height=36,
                    corner_radius=8,
                    anchor="w",
                    font=font_base,
                    fg_color="transparent",
                    hover_color=colors["sidebar_active"],
                    text_color=colors["text_secondary"],
                    command=lambda k=item.key, cb=item.callback: self._handle_click(k, cb),
                )
                btn.pack(fill="x", padx=8, pady=1)
                self._buttons[item.key] = btn

    def _handle_click(self, key: str, item_callback: Callable[[str], None] | None) -> None:
        self.set_active(key)
        if item_callback:
            item_callback(key)
        if self._on_navigate:
            self._on_navigate(key)
        logger.debug("Sidebar navigated to: %s", key)

    def _refresh_colors(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["sidebar_bg"])
        for key, btn in self._buttons.items():
            if key == self._active_key:
                btn.configure(
                    fg_color=colors["sidebar_active"],
                    text_color=colors["text_primary"],
                    hover_color=colors["sidebar_active"],
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=colors["text_secondary"],
                    hover_color=colors["sidebar_active"],
                )
