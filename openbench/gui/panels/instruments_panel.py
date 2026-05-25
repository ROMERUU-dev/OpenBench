"""Instruments dashboard content panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


@dataclass
class _InstrumentInfo:
    key: str
    name: str
    kind: str
    icon: str


_INSTRUMENTS: list[_InstrumentInfo] = [
    _InstrumentInfo("vb", "VirtualBench NI", "Oscilloscope + FGen + DC Supply", "⬡"),
    _InstrumentInfo("sr860", "SR860 Lock-in", "Lock-in Amplifier", "⬡"),
    _InstrumentInfo("keysight", "Keysight E36312A", "DC Power Supply", "⬡"),
    _InstrumentInfo("rigol", "Rigol DS1000E", "Oscilloscope", "⬡"),
    _InstrumentInfo("tektronix", "Tektronix TBS1000C", "Oscilloscope", "⬡"),
]

_STATUS_TOKEN: dict[str, str] = {
    "Connected": "success",
    "Simulated": "warning",
    "Disconnected": "error",
}


class InstrumentsPanel(ContentPanel):
    """Dashboard showing connection status for all configured backends.

    Each backend is represented by a status card. Cards support one-click
    simulation mode and disconnect actions.
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
            text="Instruments",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Manage and monitor connected lab equipment",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        ctk.CTkButton(
            header,
            text="Scan All",
            width=96,
            height=32,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self._on_scan_all,
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 0))

        # ── Scrollable card list ─────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self,
            corner_radius=0,
            fg_color=colors["bg_primary"],
            label_text="",
        )
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.columnconfigure(0, weight=1)

        self._cards: dict[str, _InstrumentCard] = {}
        for i, info in enumerate(_INSTRUMENTS):
            card = _InstrumentCard(
                self._scroll,
                info,
                on_action=self._on_card_action,
            )
            card.grid(row=i, column=0, sticky="ew", padx=24, pady=8)
            self._cards[info.key] = card

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_scan_all(self) -> None:
        logger.info("InstrumentsPanel: Scan All → Simulated")
        for card in self._cards.values():
            card.set_status("Simulated")

    def _on_card_action(self, key: str, action: str) -> None:
        logger.info("Instrument action key=%s action=%s", key, action)
        if action == "simulate":
            self._cards[key].set_status("Simulated")
        elif action == "disconnect":
            self._cards[key].set_status("Disconnected")

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_scroll"):
            self._scroll.configure(fg_color=colors["bg_primary"])


class _InstrumentCard(ctk.CTkFrame):
    """Single instrument status card with action buttons.

    Args:
        master: Parent widget.
        info: Instrument metadata.
        on_action: Callback receiving ``(key, action)`` where *action* is
            ``"simulate"`` or ``"disconnect"``.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        info: _InstrumentInfo,
        on_action: Callable[[str, str], None],
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 12)
        kwargs.setdefault("fg_color", colors["bg_card"])
        super().__init__(master, **kwargs)

        self._info = info
        self._on_action = on_action
        ff = str(theme_manager.get_font("family_fallback"))

        self.columnconfigure(1, weight=1)

        # Icon
        ctk.CTkLabel(
            self,
            text=info.icon,
            font=(ff, 26),
            text_color=colors["accent_primary"],
            width=52,
        ).grid(row=0, column=0, rowspan=2, padx=(16, 8), pady=14)

        # Name + kind
        ctk.CTkLabel(
            self,
            text=info.name,
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", padx=4, pady=(14, 0))

        ctk.CTkLabel(
            self,
            text=info.kind,
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", padx=4, pady=(0, 14))

        # Status badge
        self._status_label = ctk.CTkLabel(
            self,
            text="● Disconnected",
            font=(ff, 11),
            text_color=colors["error"],
            width=120,
            anchor="center",
        )
        self._status_label.grid(row=0, column=2, rowspan=2, padx=12)

        # Action buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=0, column=3, rowspan=2, padx=(0, 14), pady=14)

        ctk.CTkButton(
            btn_frame,
            text="Simulate",
            width=80,
            height=28,
            corner_radius=6,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=lambda: on_action(info.key, "simulate"),
        ).pack(pady=(0, 4))

        ctk.CTkButton(
            btn_frame,
            text="Disconnect",
            width=80,
            height=28,
            corner_radius=6,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=lambda: on_action(info.key, "disconnect"),
        ).pack()

        theme_manager.on_theme_change(self._refresh_theme)

    def set_status(self, status: str) -> None:
        """Update the displayed connection status badge.

        Args:
            status: One of ``"Connected"``, ``"Simulated"``, or
                ``"Disconnected"``.
        """
        colors = theme_manager.get_colors()
        token = _STATUS_TOKEN.get(status, "text_muted")
        color = colors.get(token, colors["text_muted"])
        self._status_label.configure(text=f"● {status}", text_color=color)
        logger.debug("Card %s status → %s", self._info.key, status)

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_card"])
        token = _STATUS_TOKEN.get(self._status_label.cget("text").lstrip("● ").strip(), "text_muted")
        self._status_label.configure(text_color=colors.get(token, colors["text_muted"]))
