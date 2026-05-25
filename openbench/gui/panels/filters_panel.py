"""SOFIA filter design content panel."""

from __future__ import annotations

import logging

import customtkinter as ctk

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

_TOPOLOGIES = ["Sallen-Key Lowpass", "Sallen-Key Highpass", "MFB Lowpass", "MFB Bandpass"]
_FILTER_ORDERS = ["1st order", "2nd order", "3rd order", "4th order"]


class FiltersPanel(ContentPanel):
    """SOFIA filter design and validation panel.

    Provides a quick-design form for active filter synthesis and a
    workflow summary showing the SOFIA → measurement → validation loop.
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
            text="Filters  (SOFIA)",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Design active filters and validate against measured response",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        # ── Body ─────────────────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(
            self,
            corner_radius=0,
            fg_color=colors["bg_primary"],
            label_text="",
        )
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self._body = body

        # Design form card
        form_card = ctk.CTkFrame(body, fg_color=colors["bg_card"], corner_radius=14)
        form_card.grid(row=0, column=0, sticky="nsew", padx=(24, 8), pady=16)
        form_card.columnconfigure(1, weight=1)
        self._form_card = form_card

        ctk.CTkLabel(
            form_card,
            text="Quick Design",
            font=(ff, 14, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(16, 12))

        self._topology_var = ctk.StringVar(value=_TOPOLOGIES[0])
        self._order_var = ctk.StringVar(value=_FILTER_ORDERS[1])
        self._fc_var = ctk.StringVar(value="1000")
        self._q_var = ctk.StringVar(value="0.707")

        _fields = [
            ("Topology", "menu", _TOPOLOGIES, self._topology_var),
            ("Order", "menu", _FILTER_ORDERS, self._order_var),
            ("Cutoff freq (Hz)", "entry", None, self._fc_var),
            ("Q factor", "entry", None, self._q_var),
        ]
        for row_i, (label, kind, choices, var) in enumerate(_fields, start=1):
            ctk.CTkLabel(
                form_card,
                text=label,
                font=(ff, 12),
                text_color=colors["text_secondary"],
                anchor="w",
            ).grid(row=row_i, column=0, sticky="w", padx=16, pady=4)

            if kind == "menu":
                widget = ctk.CTkOptionMenu(
                    form_card,
                    values=choices,
                    variable=var,
                    height=30,
                    corner_radius=6,
                )
            else:
                widget = ctk.CTkEntry(
                    form_card,
                    textvariable=var,
                    height=30,
                    corner_radius=6,
                    fg_color=colors["bg_input"],
                )
            widget.grid(row=row_i, column=1, sticky="ew", padx=(8, 16), pady=4)

        ctk.CTkButton(
            form_card,
            text="Design Filter",
            height=34,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self._on_design,
        ).grid(row=len(_fields) + 1, column=0, columnspan=2, padx=16, pady=(12, 16), sticky="ew")

        # Workflow summary card
        wf_card = ctk.CTkFrame(body, fg_color=colors["bg_card"], corner_radius=14)
        wf_card.grid(row=0, column=1, sticky="nsew", padx=(8, 24), pady=16)
        wf_card.columnconfigure(0, weight=1)
        self._wf_card = wf_card

        ctk.CTkLabel(
            wf_card,
            text="SOFIA Workflow",
            font=(ff, 14, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))

        _steps = [
            ("1", "Design filter topology and parameters"),
            ("2", "Generate measurement setup automatically"),
            ("3", "Measure response via VirtualBench / SR860"),
            ("4", "Compare theory vs. measured, export report"),
        ]
        for step_i, (num, text) in enumerate(_steps, start=1):
            step_row = ctk.CTkFrame(wf_card, fg_color="transparent")
            step_row.grid(row=step_i, column=0, sticky="ew", padx=16, pady=4)

            ctk.CTkLabel(
                step_row,
                text=num,
                width=24,
                height=24,
                corner_radius=12,
                fg_color=colors["accent_primary"],
                text_color=colors["text_on_accent"],
                font=(ff, 11, "bold"),
            ).pack(side="left", padx=(0, 10))

            ctk.CTkLabel(
                step_row,
                text=text,
                font=(ff, 12),
                text_color=colors["text_secondary"],
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkFrame(wf_card, height=1, fg_color=colors["border"]).grid(
            row=len(_steps) + 1, column=0, sticky="ew", padx=16, pady=8
        )

        self._result_label = ctk.CTkLabel(
            wf_card,
            text="No design active.",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
            wraplength=220,
            justify="left",
        )
        self._result_label.grid(row=len(_steps) + 2, column=0, sticky="w", padx=16, pady=(0, 16))

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_design(self) -> None:
        topology = self._topology_var.get()
        fc = self._fc_var.get()
        order = self._order_var.get()
        q = self._q_var.get()
        logger.info("FilterPanel: design requested topology=%s fc=%s order=%s Q=%s", topology, fc, order, q)
        self._result_label.configure(
            text=f"Design ready: {topology}, {order}, fc={fc} Hz, Q={q}",
            text_color=theme_manager.get_color("success"),
        )

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_body"):
            self._body.configure(fg_color=colors["bg_primary"])
        for card in (getattr(self, "_form_card", None), getattr(self, "_wf_card", None)):
            if card is not None:
                card.configure(fg_color=colors["bg_card"])
