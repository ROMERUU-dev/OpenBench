"""Welcome / landing page content panel."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

_QUICK_CARDS: list[tuple[str, str, str]] = [
    ("◈", "Instruments", "Connect and monitor\nlab equipment"),
    ("▶", "Experiments", "Run automated\nmeasurement routines"),
    ("◇", "Filters", "Design active filters\nwith SOFIA"),
    ("☰", "Data", "Browse sessions\nand measurement plots"),
]


class WelcomePanel(ContentPanel):
    """Landing page displayed at startup and as the default fallback panel.

    Shows the application name, a short description, and quick-action
    summary cards for each major section.
    """

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.grid(row=1, column=0)

        ctk.CTkLabel(
            inner,
            text="OpenBench",
            font=(ff, 34, "bold"),
            text_color=colors["accent_primary"],
        ).pack(pady=(0, 6))

        ctk.CTkLabel(
            inner,
            text="Lab Instrument Orchestration Platform",
            font=(ff, 14),
            text_color=colors["text_muted"],
        ).pack(pady=(0, 40))

        cards_row = ctk.CTkFrame(inner, fg_color="transparent")
        cards_row.pack()
        self._cards_row = cards_row
        self._cards: list[ctk.CTkFrame] = []

        for icon, title, desc in _QUICK_CARDS:
            card = ctk.CTkFrame(
                cards_row,
                fg_color=colors["bg_card"],
                corner_radius=14,
                width=160,
                height=110,
            )
            card.pack(side="left", padx=8)
            card.pack_propagate(False)
            self._cards.append(card)

            ctk.CTkLabel(
                card,
                text=f"{icon}  {title}",
                font=(ff, 13, "bold"),
                text_color=colors["text_primary"],
                anchor="w",
            ).pack(padx=14, pady=(14, 4), anchor="w")

            ctk.CTkLabel(
                card,
                text=desc,
                font=(ff, 11),
                text_color=colors["text_muted"],
                anchor="w",
                justify="left",
            ).pack(padx=14, anchor="w")

        ctk.CTkLabel(
            inner,
            text="Select an item from the sidebar to get started.",
            font=(ff, 12),
            text_color=colors["text_muted"],
        ).pack(pady=(32, 0))

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        for card in self._cards:
            card.configure(fg_color=colors["bg_card"])
