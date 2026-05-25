"""Experiments launcher content panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)


@dataclass
class _ExperimentInfo:
    key: str
    name: str
    description: str
    instruments: str
    icon: str = "▶"


_EXPERIMENTS: list[_ExperimentInfo] = [
    _ExperimentInfo(
        key="dc_sweep",
        name="DC Sweep",
        description="Sweep DC voltage and record I(V) characteristic.",
        instruments="Keysight E36312A",
    ),
    _ExperimentInfo(
        key="freq_sweep",
        name="Frequency Sweep",
        description="Sweep signal frequency and record amplitude/phase response.",
        instruments="SR860 + VirtualBench FGen",
    ),
    _ExperimentInfo(
        key="imp_sweep",
        name="Impedance Sweep",
        description="Measure complex impedance Z(f) over a frequency range.",
        instruments="SR860 Lock-in",
    ),
    _ExperimentInfo(
        key="chua",
        name="Chua Admittance",
        description="Sweep admittance Y(f, Vbias) for Chua circuit characterization.",
        instruments="SR860 + Keysight bias",
    ),
    _ExperimentInfo(
        key="comp_char",
        name="Component Characterization",
        description="Full characterization of TC4069UBP inverter gates.",
        instruments="Keysight DC + VirtualBench scope",
    ),
]


class ExperimentsPanel(ContentPanel):
    """Lists available experiment routines with run controls.

    Each experiment card shows its name, required instruments, and a
    Run button that logs the launch event (hardware execution wired
    separately via the orchestrator).
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
            text="Experiments",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Automated measurement routines",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        # ── Scrollable experiment list ───────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self,
            corner_radius=0,
            fg_color=colors["bg_primary"],
            label_text="",
        )
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.columnconfigure(0, weight=1)

        self._exp_cards: dict[str, _ExperimentCard] = {}
        for i, info in enumerate(_EXPERIMENTS):
            card = _ExperimentCard(self._scroll, info, on_run=self._on_run)
            card.grid(row=i, column=0, sticky="ew", padx=24, pady=8)
            self._exp_cards[info.key] = card

    def _on_run(self, key: str) -> None:
        logger.info("ExperimentsPanel: run requested for %s", key)
        card = self._exp_cards.get(key)
        if card:
            card.set_state("running")

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_scroll"):
            self._scroll.configure(fg_color=colors["bg_primary"])


class _ExperimentCard(ctk.CTkFrame):
    """Experiment entry card with description and Run button.

    Args:
        master: Parent widget.
        info: Experiment metadata.
        on_run: Callback receiving the experiment key when Run is clicked.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        info: _ExperimentInfo,
        on_run: ...,
        **kwargs,
    ) -> None:
        colors = theme_manager.get_colors()
        kwargs.setdefault("corner_radius", 12)
        kwargs.setdefault("fg_color", colors["bg_card"])
        super().__init__(master, **kwargs)

        self._info = info
        self._on_run = on_run
        ff = str(theme_manager.get_font("family_fallback"))

        self.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=info.icon,
            font=(ff, 22),
            text_color=colors["accent_secondary"],
            width=48,
        ).grid(row=0, column=0, rowspan=2, padx=(16, 8), pady=14)

        ctk.CTkLabel(
            self,
            text=info.name,
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", padx=4, pady=(14, 2))

        ctk.CTkLabel(
            self,
            text=f"{info.description}  —  {info.instruments}",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", padx=4, pady=(0, 14))

        self._run_btn = ctk.CTkButton(
            self,
            text="▶  Run",
            width=80,
            height=30,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=lambda: on_run(info.key),
        )
        self._run_btn.grid(row=0, column=2, rowspan=2, padx=(0, 16))

        theme_manager.on_theme_change(self._refresh_theme)

    def set_state(self, state: str) -> None:
        """Update the visual state of the card.

        Args:
            state: ``"idle"`` or ``"running"``.
        """
        colors = theme_manager.get_colors()
        if state == "running":
            self._run_btn.configure(text="⏸  Running", fg_color=colors["warning"])
        else:
            self._run_btn.configure(text="▶  Run", fg_color=colors["accent_primary"])
        logger.debug("ExperimentCard %s state → %s", self._info.key, state)

    def _refresh_theme(self, _mode: str) -> None:
        self.configure(fg_color=theme_manager.get_color("bg_card"))
