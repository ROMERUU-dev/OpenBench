"""Data management content panel (sessions and plots)."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

_PLACEHOLDER_SESSIONS = [
    ("2026-05-24 14:32", "Chua admittance sweep", "12 points", "SR860 + Keysight"),
    ("2026-05-24 11:15", "TC4069UBP characterization", "50 points", "Keysight + VB"),
    ("2026-05-23 16:48", "Inductor frequency sweep", "100 points", "SR860"),
]


class DataPanel(ContentPanel):
    """Displays recorded measurement sessions and quick plot access.

    Shows a recent-sessions list with metadata and placeholder export
    actions. Content is populated with example data when no real sessions
    are present.
    """

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)

        # ── Header ──────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent", height=72)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Data",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Measurement sessions and plots",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        ctk.CTkButton(
            header,
            text="Export All",
            width=96,
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self._on_export_all,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        # ── Body ─────────────────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(
            self,
            corner_radius=0,
            fg_color=colors["bg_primary"],
            label_text="",
        )
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        self._body = body

        # Sessions section
        ctk.CTkLabel(
            body,
            text="Recent Sessions",
            font=(ff, 14, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(16, 8))

        self._session_rows: list[ctk.CTkFrame] = []
        for i, (ts, name, points, instruments) in enumerate(_PLACEHOLDER_SESSIONS, start=1):
            row = self._make_session_row(body, ts, name, points, instruments)
            row.grid(row=i, column=0, sticky="ew", padx=24, pady=4)
            self._session_rows.append(row)

        # Empty state hint
        ctk.CTkLabel(
            body,
            text="Run an experiment to record a new session.",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=len(_PLACEHOLDER_SESSIONS) + 1, column=0, sticky="w", padx=24, pady=(12, 0))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_session_row(
        self,
        master: ctk.CTkBaseClass,
        timestamp: str,
        name: str,
        points: str,
        instruments: str,
    ) -> ctk.CTkFrame:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        card = ctk.CTkFrame(master, fg_color=colors["bg_card"], corner_radius=10)
        card.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="☰",
            font=(ff, 18),
            text_color=colors["text_muted"],
            width=40,
        ).grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=12)

        ctk.CTkLabel(
            card,
            text=name,
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", pady=(12, 1))

        ctk.CTkLabel(
            card,
            text=f"{timestamp}  ·  {points}  ·  {instruments}",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", pady=(0, 12))

        ctk.CTkButton(
            card,
            text="Plot",
            width=60,
            height=28,
            corner_radius=6,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=lambda n=name: logger.info("DataPanel: plot %r", n),
        ).grid(row=0, column=2, rowspan=2, padx=(0, 12))

        return card

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_export_all(self) -> None:
        logger.info("DataPanel: Export All triggered")

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_body"):
            self._body.configure(fg_color=colors["bg_primary"])
        for row in getattr(self, "_session_rows", []):
            row.configure(fg_color=colors["bg_card"])
