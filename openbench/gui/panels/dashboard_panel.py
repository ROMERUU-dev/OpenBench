"""System-wide instrument dashboard content panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import FONTS, theme_manager

logger = logging.getLogger(__name__)

# Refresh interval in milliseconds.
_REFRESH_INTERVAL_MS = 5_000

_STATUS_COLOR_KEY: dict[str, str] = {
    "Connected": "success",
    "Simulated": "warning",
    "Disconnected": "error",
    "Unknown": "text_muted",
}

_STATUS_DOT: dict[str, str] = {
    "Connected": "●",
    "Simulated": "◉",
    "Disconnected": "○",
    "Unknown": "○",
}


@dataclass
class InstrumentState:
    """Snapshot of a single instrument's dashboard state.

    Attributes:
        key: Stable identifier used for lookup.
        name: Human-readable display name.
        kind: Instrument type description.
        icon: Unicode glyph shown in the card.
        status: Current connection status string.
    """

    key: str
    name: str
    kind: str
    icon: str
    status: str = "Disconnected"


@dataclass
class _QuickAction:
    label: str
    icon: str
    callback_key: str


_DEFAULT_INSTRUMENTS: list[InstrumentState] = [
    InstrumentState("vb", "VirtualBench NI", "Osc + FGen + DC", "⬡"),
    InstrumentState("sr860", "SR860 Lock-in", "Lock-in Amplifier", "⬡"),
    InstrumentState("keysight", "Keysight E36312A", "DC Power Supply", "⬡"),
    InstrumentState("rigol", "Rigol DS1000E", "Oscilloscope", "⬡"),
    InstrumentState("tektronix", "Tektronix TBS1000C", "Oscilloscope", "⬡"),
]

_QUICK_ACTIONS: list[_QuickAction] = [
    _QuickAction("Scan Instruments", "◈", "scan_all"),
    _QuickAction("Simulate All", "⬡", "simulate_all"),
    _QuickAction("Run Chua Workflow", "▶", "run_chua"),
    _QuickAction("Open Data", "☰", "open_data"),
]


class DashboardPanel(ContentPanel):
    """System-wide overview panel showing instrument status and quick actions.

    Displays four summary stat cards at the top (total/connected/simulated/
    disconnected counts), a two-column compact instrument grid, and a quick-
    action sidebar. Auto-refreshes status display every
    ``_REFRESH_INTERVAL_MS`` milliseconds while visible.

    Args:
        master: Parent CustomTkinter widget.
        on_quick_action: Optional callback receiving the action key string
            when a quick-action button is clicked.
        **kwargs: Forwarded to ``ContentPanel``.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_quick_action: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        self._on_quick_action = on_quick_action
        self._instruments: list[InstrumentState] = [
            InstrumentState(s.key, s.name, s.kind, s.icon, s.status)
            for s in _DEFAULT_INSTRUMENTS
        ]
        self._last_refresh: datetime | None = None
        self._refresh_job: str | None = None
        super().__init__(master, **kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_instrument_status(self, key: str, status: str) -> None:
        """Update one instrument's status and refresh its card.

        Args:
            key: Instrument key matching an ``InstrumentState.key``.
            status: New status string — ``"Connected"``, ``"Simulated"``,
                or ``"Disconnected"``.
        """
        for state in self._instruments:
            if state.key == key:
                state.status = status
                break
        else:
            logger.warning("DashboardPanel.set_instrument_status: unknown key %r", key)
            return

        card = self._inst_cards.get(key)
        if card:
            card.set_status(status)
        self._update_stat_cards()
        logger.debug("Dashboard instrument %r → %s", key, status)

    def set_all_status(self, status: str) -> None:
        """Set the same status on every instrument.

        Args:
            status: Target status string.
        """
        for state in self._instruments:
            state.status = status
        for card in self._inst_cards.values():
            card.set_status(status)
        self._update_stat_cards()
        logger.debug("Dashboard: all instruments set to %s", status)

    # ------------------------------------------------------------------
    # ContentPanel lifecycle
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        """Start the auto-refresh loop when this panel becomes active."""
        super().on_show()
        self._schedule_refresh()

    def on_hide(self) -> None:
        """Cancel the auto-refresh loop when the panel is hidden."""
        super().on_hide()
        self._cancel_refresh()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self.rowconfigure(0, weight=0)  # header
        self.rowconfigure(1, weight=0)  # stat strip
        self.rowconfigure(2, weight=1)  # body
        self.rowconfigure(3, weight=0)  # footer status

        # ── Header ──────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent", height=72)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Dashboard",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="System overview — instruments and quick actions",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        refresh_btn = ctk.CTkButton(
            header,
            text="↺  Refresh",
            width=96,
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self._on_manual_refresh,
        )
        refresh_btn.grid(row=0, column=1, rowspan=2, sticky="e")

        # ── Stat strip ──────────────────────────────────────────────────
        stat_strip = ctk.CTkFrame(self, fg_color="transparent")
        stat_strip.grid(row=1, column=0, sticky="ew", padx=24, pady=(16, 0))
        for col in range(4):
            stat_strip.columnconfigure(col, weight=1)

        self._stat_cards: list[_StatCard] = []
        labels = [
            ("Total", "text_muted", "◻"),
            ("Connected", "success", "●"),
            ("Simulated", "warning", "◉"),
            ("Disconnected", "error", "○"),
        ]
        for col, (title, color_key, dot) in enumerate(labels):
            card = _StatCard(stat_strip, title=title, dot=dot, color_key=color_key)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
            self._stat_cards.append(card)

        # ── Body (instrument grid + quick actions) ───────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=24, pady=16)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)

        # Instruments scrollable grid
        inst_frame = ctk.CTkFrame(body, fg_color="transparent")
        inst_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        inst_frame.rowconfigure(0, weight=0)
        inst_frame.rowconfigure(1, weight=1)
        inst_frame.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            inst_frame,
            text="Instruments",
            font=(ff, 13, "bold"),
            text_color=colors["text_secondary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        self._inst_scroll = ctk.CTkScrollableFrame(
            inst_frame,
            corner_radius=8,
            fg_color=colors["bg_secondary"],
            label_text="",
        )
        self._inst_scroll.grid(row=1, column=0, sticky="nsew")
        self._inst_scroll.columnconfigure(0, weight=1)
        self._inst_scroll.columnconfigure(1, weight=1)

        self._inst_cards: dict[str, _InstrumentMiniCard] = {}
        for idx, state in enumerate(self._instruments):
            row_i, col_i = divmod(idx, 2)
            card = _InstrumentMiniCard(self._inst_scroll, state)
            card.grid(row=row_i, column=col_i, sticky="ew", padx=6, pady=4)
            self._inst_cards[state.key] = card

        # Quick actions
        actions_frame = ctk.CTkFrame(body, fg_color="transparent")
        actions_frame.grid(row=0, column=1, sticky="nsew")
        actions_frame.rowconfigure(0, weight=0)
        actions_frame.rowconfigure(1, weight=1)
        actions_frame.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            actions_frame,
            text="Quick Actions",
            font=(ff, 13, "bold"),
            text_color=colors["text_secondary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        qa_inner = ctk.CTkFrame(
            actions_frame,
            fg_color=colors["bg_secondary"],
            corner_radius=8,
        )
        qa_inner.grid(row=1, column=0, sticky="nsew")
        qa_inner.columnconfigure(0, weight=1)
        self._qa_inner = qa_inner

        for i, action in enumerate(_QUICK_ACTIONS):
            ctk.CTkButton(
                qa_inner,
                text=f"{action.icon}  {action.label}",
                height=36,
                corner_radius=8,
                anchor="w",
                fg_color="transparent",
                hover_color=colors["sidebar_active"],
                text_color=colors["text_primary"],
                font=(ff, 12),
                command=lambda k=action.callback_key: self._dispatch_action(k),
            ).grid(row=i, column=0, sticky="ew", padx=8, pady=(8 if i == 0 else 4, 4))

        # ── Footer ──────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent", height=28)
        footer.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 8))
        footer.grid_propagate(False)
        footer.columnconfigure(0, weight=1)

        self._refresh_label = ctk.CTkLabel(
            footer,
            text="",
            font=(ff, 10),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._refresh_label.grid(row=0, column=0, sticky="w")

        self._update_stat_cards()
        self._update_refresh_label()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_stat_cards(self) -> None:
        total = len(self._instruments)
        connected = sum(1 for s in self._instruments if s.status == "Connected")
        simulated = sum(1 for s in self._instruments if s.status == "Simulated")
        disconnected = sum(1 for s in self._instruments if s.status == "Disconnected")

        counts = [total, connected, simulated, disconnected]
        for card, count in zip(self._stat_cards, counts):
            card.set_value(count)

    def _update_refresh_label(self) -> None:
        if self._last_refresh is None:
            text = "Not yet refreshed"
        else:
            ts = self._last_refresh.strftime("%H:%M:%S")
            text = f"Last refresh: {ts}"
        if hasattr(self, "_refresh_label"):
            self._refresh_label.configure(text=text)

    def _on_manual_refresh(self) -> None:
        self._last_refresh = datetime.now()
        self._update_refresh_label()
        logger.info("DashboardPanel: manual refresh triggered")

    def _schedule_refresh(self) -> None:
        self._cancel_refresh()
        try:
            self._refresh_job = self.after(_REFRESH_INTERVAL_MS, self._auto_refresh)
        except Exception:
            pass

    def _cancel_refresh(self) -> None:
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None

    def _auto_refresh(self) -> None:
        self._last_refresh = datetime.now()
        self._update_refresh_label()
        self._update_stat_cards()
        logger.debug("DashboardPanel: auto-refresh tick")
        self._schedule_refresh()

    def _dispatch_action(self, key: str) -> None:
        logger.info("DashboardPanel: quick action %r", key)
        if key == "scan_all":
            self.set_all_status("Disconnected")
        elif key == "simulate_all":
            self.set_all_status("Simulated")
        if self._on_quick_action is not None:
            self._on_quick_action(key)

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_inst_scroll"):
            self._inst_scroll.configure(fg_color=colors["bg_secondary"])
        if hasattr(self, "_qa_inner"):
            self._qa_inner.configure(fg_color=colors["bg_secondary"])


class _StatCard(ctk.CTkFrame):
    """Compact numeric summary card for the dashboard stat strip.

    Args:
        master: Parent widget.
        title: Label shown below the count.
        dot: Status indicator glyph shown next to the count.
        color_key: Theme palette key controlling the count text color.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        title: str,
        dot: str,
        color_key: str,
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("fg_color", colors["bg_card"])
        super().__init__(master, **kwargs)

        self._color_key = color_key
        ff = str(theme_manager.get_font("family_fallback"))

        self.columnconfigure(0, weight=1)

        self._value_label = ctk.CTkLabel(
            self,
            text=f"{dot} 0",
            font=(ff, 22, "bold"),
            text_color=colors.get(color_key, colors["text_primary"]),
            anchor="center",
        )
        self._value_label.grid(row=0, column=0, padx=12, pady=(14, 2))

        ctk.CTkLabel(
            self,
            text=title,
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="center",
        ).grid(row=1, column=0, padx=12, pady=(0, 12))

        self._dot = dot
        theme_manager.on_theme_change(self._refresh_theme)

    def set_value(self, count: int) -> None:
        """Update the displayed count.

        Args:
            count: New numeric value to display.
        """
        self._value_label.configure(text=f"{self._dot} {count}")

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_card"])
        self._value_label.configure(
            text_color=colors.get(self._color_key, colors["text_primary"])
        )


class _InstrumentMiniCard(ctk.CTkFrame):
    """Compact instrument card for the dashboard grid.

    Shows instrument icon, name, kind, and a colored status badge.

    Args:
        master: Parent widget.
        state: Instrument state snapshot.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        state: InstrumentState,
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("fg_color", colors["bg_card"])
        super().__init__(master, **kwargs)

        self._state = state
        ff = str(theme_manager.get_font("family_fallback"))

        self.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=state.icon,
            font=(ff, 20),
            text_color=colors["accent_primary"],
            width=36,
        ).grid(row=0, column=0, rowspan=2, padx=(10, 6), pady=10)

        ctk.CTkLabel(
            self,
            text=state.name,
            font=(ff, 11, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", pady=(10, 1))

        ctk.CTkLabel(
            self,
            text=state.kind,
            font=(ff, 10),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", pady=(0, 10))

        status_color = colors.get(
            _STATUS_COLOR_KEY.get(state.status, "text_muted"), colors["text_muted"]
        )
        dot = _STATUS_DOT.get(state.status, "○")
        self._status_lbl = ctk.CTkLabel(
            self,
            text=f"{dot} {state.status}",
            font=(ff, 10),
            text_color=status_color,
            width=90,
            anchor="center",
        )
        self._status_lbl.grid(row=0, column=2, rowspan=2, padx=(0, 10))

        theme_manager.on_theme_change(self._refresh_theme)

    def set_status(self, status: str) -> None:
        """Update the displayed status badge.

        Args:
            status: One of ``"Connected"``, ``"Simulated"``, or
                ``"Disconnected"``.
        """
        colors = theme_manager.get_colors()
        color_key = _STATUS_COLOR_KEY.get(status, "text_muted")
        color = colors.get(color_key, colors["text_muted"])
        dot = _STATUS_DOT.get(status, "○")
        self._status_lbl.configure(text=f"{dot} {status}", text_color=color)
        self._state.status = status

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_card"])
        color_key = _STATUS_COLOR_KEY.get(self._state.status, "text_muted")
        self._status_lbl.configure(
            text_color=colors.get(color_key, colors["text_muted"])
        )


__all__ = ["DashboardPanel", "InstrumentState"]
