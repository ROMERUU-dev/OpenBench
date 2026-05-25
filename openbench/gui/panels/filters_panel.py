"""SOFIA filter design content panel."""

from __future__ import annotations

import logging
import threading

import customtkinter as ctk
import numpy as np

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

try:
    from openbench.filters import (
        Approximation,
        DesignInputs,
        FilterDesigner,
        FilterKind,
        FilterSpec,
        FilterValidator,
        Topology,
    )

    _SOFIA_AVAILABLE = True
except ImportError:
    _SOFIA_AVAILABLE = False
    logger.warning("sofia_filter_studio not available; filter design disabled")

_KIND_LABELS = ["Low-Pass", "High-Pass", "Band-Pass", "Band-Stop"]
_APPROX_LABELS = ["Butterworth", "Chebyshev", "Elliptic", "Bessel"]
_TOPOLOGY_LABELS = ["Sallen-Key", "MFB"]


def _parse_float(value: str, name: str) -> float:
    """Parse ``value`` as a float with a descriptive error on failure.

    Args:
        value: String to parse.
        name: Human-readable field name used in the error message.

    Returns:
        Parsed float value.

    Raises:
        ValueError: When ``value`` cannot be converted to a float.
    """
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"'{name}' must be a number, got {value!r}")


def _format_stage(stage: object) -> str:
    """Format a SOFIA StageRealization for single-line display.

    Args:
        stage: SOFIA ``StageRealization`` object.

    Returns:
        Human-readable string summarising available stage attributes.
    """
    parts: list[str] = []
    for attr in ("q_factor", "fc_hz", "gain", "order"):
        val = getattr(stage, attr, None)
        if val is not None:
            parts.append(f"{attr}={val:.3g}")
    return ", ".join(parts) if parts else repr(stage)


class FiltersPanel(ContentPanel):
    """SOFIA filter design and validation panel.

    Full-featured active filter synthesis workflow:

    - Form-driven ``DesignInputs`` construction
    - SOFIA synthesis engine (background thread, non-blocking)
    - Theoretical Bode plot via ``FilterValidator``
    - Measurement setup suggestions for SR860 / VirtualBench
    - SPICE netlist with clipboard export

    Args:
        master: Parent CustomTkinter widget.
        **kwargs: Forwarded to ``ContentPanel``.
    """

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self._current_designer = None
        self._current_result = None
        self._netlist: str = ""
        self._bode_canvas_obj = None

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
            text="Active filter synthesis  ·  Bode plot  ·  SPICE netlist  ·  Measurement setup",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w")

        # ── Scrollable body ──────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(
            self,
            corner_radius=0,
            fg_color=colors["bg_primary"],
            label_text="",
        )
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        self._body = body

        self._build_form_card(body, colors, ff)
        self._build_results_area(body, colors, ff)

    # ------------------------------------------------------------------
    # Widget construction helpers
    # ------------------------------------------------------------------

    def _build_form_card(
        self,
        parent: ctk.CTkScrollableFrame,
        colors: dict,
        ff: str,
    ) -> None:
        """Construct the left-column filter parameter form.

        Args:
            parent: Scrollable body frame.
            colors: Current theme color palette.
            ff: Font family string.
        """
        form_card = ctk.CTkFrame(parent, fg_color=colors["bg_card"], corner_radius=14)
        form_card.grid(row=0, column=0, sticky="new", padx=(24, 8), pady=16)
        form_card.columnconfigure(1, weight=1)
        self._form_card = form_card

        ctk.CTkLabel(
            form_card,
            text="Filter Parameters",
            font=(ff, 14, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(16, 8))

        self._kind_var = ctk.StringVar(value="Low-Pass")
        self._approx_var = ctk.StringVar(value="Butterworth")
        self._topology_var = ctk.StringVar(value="Sallen-Key")
        self._passband_var = ctk.StringVar(value="1000")
        self._stopband_var = ctk.StringVar(value="5000")
        self._ripple_var = ctk.StringVar(value="1.0")
        self._atten_var = ctk.StringVar(value="40.0")

        row_i = 1

        _menu_fields = [
            ("Filter Kind", _KIND_LABELS, self._kind_var),
            ("Approximation", _APPROX_LABELS, self._approx_var),
            ("Topology", _TOPOLOGY_LABELS, self._topology_var),
        ]
        for label, choices, var in _menu_fields:
            ctk.CTkLabel(
                form_card,
                text=label,
                font=(ff, 12),
                text_color=colors["text_secondary"],
                anchor="w",
            ).grid(row=row_i, column=0, sticky="w", padx=16, pady=(6, 0))
            ctk.CTkOptionMenu(
                form_card,
                values=choices,
                variable=var,
                height=30,
                corner_radius=6,
            ).grid(row=row_i, column=1, sticky="ew", padx=(8, 16), pady=(6, 0))
            row_i += 1

        ctk.CTkFrame(form_card, height=1, fg_color=colors["border"]).grid(
            row=row_i, column=0, columnspan=2, sticky="ew", padx=16, pady=10
        )
        row_i += 1

        _entry_fields = [
            ("Passband freq (Hz)", self._passband_var),
            ("Stopband freq (Hz)", self._stopband_var),
            ("Passband ripple (dB)", self._ripple_var),
            ("Stopband atten (dB)", self._atten_var),
        ]
        for label, var in _entry_fields:
            ctk.CTkLabel(
                form_card,
                text=label,
                font=(ff, 12),
                text_color=colors["text_secondary"],
                anchor="w",
            ).grid(row=row_i, column=0, sticky="w", padx=16, pady=(4, 0))
            ctk.CTkEntry(
                form_card,
                textvariable=var,
                height=30,
                corner_radius=6,
                fg_color=colors["bg_input"],
            ).grid(row=row_i, column=1, sticky="ew", padx=(8, 16), pady=(4, 0))
            row_i += 1

        ctk.CTkFrame(form_card, height=1, fg_color=colors["border"]).grid(
            row=row_i, column=0, columnspan=2, sticky="ew", padx=16, pady=10
        )
        row_i += 1

        sofia_state = "normal" if _SOFIA_AVAILABLE else "disabled"
        sofia_msg = "" if _SOFIA_AVAILABLE else "sofia_filter_studio not installed."

        self._status_label = ctk.CTkLabel(
            form_card,
            text=sofia_msg,
            font=(ff, 11),
            text_color=colors["error"] if not _SOFIA_AVAILABLE else colors["text_muted"],
            anchor="w",
            wraplength=240,
            justify="left",
        )
        self._status_label.grid(
            row=row_i, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 6)
        )
        row_i += 1

        self._design_btn = ctk.CTkButton(
            form_card,
            text="Design Filter",
            height=36,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self._on_design,
            state=sofia_state,
        )
        self._design_btn.grid(
            row=row_i, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="ew"
        )

    def _build_results_area(
        self,
        parent: ctk.CTkScrollableFrame,
        colors: dict,
        ff: str,
    ) -> None:
        """Construct the right-column results area.

        Stacks four cards vertically: Bode plot, design info,
        measurement setup, and SPICE netlist.

        Args:
            parent: Scrollable body frame.
            colors: Current theme color palette.
            ff: Font family string.
        """
        results_col = ctk.CTkFrame(parent, fg_color="transparent")
        results_col.grid(row=0, column=1, sticky="nsew", padx=(8, 24), pady=16)
        results_col.columnconfigure(0, weight=1)
        self._results_col = results_col

        # ── Bode plot card ───────────────────────────────────────────────
        plot_card = ctk.CTkFrame(results_col, fg_color=colors["bg_card"], corner_radius=14)
        plot_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        plot_card.columnconfigure(0, weight=1)
        self._plot_card = plot_card

        ctk.CTkLabel(
            plot_card,
            text="Theoretical Bode Plot",
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))

        self._bode_placeholder = ctk.CTkLabel(
            plot_card,
            text="Design a filter to see the Bode plot.",
            font=(ff, 12),
            text_color=colors["text_muted"],
            height=220,
        )
        self._bode_placeholder.grid(row=1, column=0, padx=16, pady=(0, 16))

        # ── Design info card ─────────────────────────────────────────────
        info_card = ctk.CTkFrame(results_col, fg_color=colors["bg_card"], corner_radius=14)
        info_card.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        info_card.columnconfigure(0, weight=1)
        self._info_card = info_card

        ctk.CTkLabel(
            info_card,
            text="Design Results",
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))

        self._info_label = ctk.CTkLabel(
            info_card,
            text="No design active.",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
            justify="left",
            wraplength=400,
        )
        self._info_label.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 16))

        # ── Measurement setup card ───────────────────────────────────────
        meas_card = ctk.CTkFrame(results_col, fg_color=colors["bg_card"], corner_radius=14)
        meas_card.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        meas_card.columnconfigure(0, weight=1)
        self._meas_card = meas_card

        ctk.CTkLabel(
            meas_card,
            text="Measurement Setup",
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))

        self._meas_label = ctk.CTkLabel(
            meas_card,
            text="Run design to see suggested measurement parameters.",
            font=(ff, 11),
            text_color=colors["text_muted"],
            anchor="w",
            justify="left",
            wraplength=400,
        )
        self._meas_label.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 16))

        # ── Netlist card ─────────────────────────────────────────────────
        netlist_card = ctk.CTkFrame(results_col, fg_color=colors["bg_card"], corner_radius=14)
        netlist_card.grid(row=3, column=0, sticky="nsew")
        netlist_card.columnconfigure(0, weight=1)
        self._netlist_card = netlist_card

        netlist_hdr = ctk.CTkFrame(netlist_card, fg_color="transparent")
        netlist_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        netlist_hdr.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            netlist_hdr,
            text="SPICE Netlist",
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self._copy_btn = ctk.CTkButton(
            netlist_hdr,
            text="Copy",
            width=64,
            height=26,
            corner_radius=6,
            fg_color=colors["accent_secondary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self._on_copy_netlist,
            state="disabled",
        )
        self._copy_btn.grid(row=0, column=1)

        fm = str(theme_manager.get_font("family_mono"))
        self._netlist_text = ctk.CTkTextbox(
            netlist_card,
            height=160,
            corner_radius=8,
            fg_color=colors["bg_input"],
            text_color=colors["text_secondary"],
            font=(fm, 10),
            state="disabled",
        )
        self._netlist_text.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------

    def _on_design(self) -> None:
        """Validate form inputs and launch SOFIA synthesis in a worker thread."""
        if not _SOFIA_AVAILABLE:
            self._set_status("SOFIA not installed — design unavailable.", error=True)
            return

        kind_label = self._kind_var.get()
        if kind_label in ("Band-Pass", "Band-Stop"):
            self._set_status(
                "Band-Pass/Stop require tuple passband_hz. Use Low-Pass or High-Pass for quick design.",
                error=True,
            )
            return

        try:
            passband_hz = _parse_float(self._passband_var.get(), "Passband freq")
            stopband_hz = _parse_float(self._stopband_var.get(), "Stopband freq")
            ripple_db = _parse_float(self._ripple_var.get(), "Passband ripple")
            atten_db = _parse_float(self._atten_var.get(), "Stopband atten")
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return

        approx_label = self._approx_var.get()
        topology_label = self._topology_var.get()

        kind = {"Low-Pass": FilterKind.LOWPASS, "High-Pass": FilterKind.HIGHPASS}[kind_label]
        approx = {
            "Butterworth": Approximation.BUTTERWORTH,
            "Chebyshev": Approximation.CHEBYSHEV,
            "Elliptic": Approximation.ELLIPTIC,
            "Bessel": Approximation.BESSEL,
        }[approx_label]
        topology = {"Sallen-Key": Topology.SALLEN_KEY, "MFB": Topology.MFB}[topology_label]

        try:
            inputs = DesignInputs(
                kind=kind,
                approximation=approx,
                spec=FilterSpec(passband_hz=passband_hz, stopband_hz=stopband_hz),
                passband_ripple_db=ripple_db,
                stopband_attenuation_db=atten_db,
                topology=topology,
            )
        except Exception as exc:
            self._set_status(f"Invalid parameters: {exc}", error=True)
            return

        self._design_btn.configure(state="disabled", text="Designing…")
        self._set_status("Running SOFIA synthesis…")
        logger.info(
            "FiltersPanel: SOFIA design — %s %s fc=%.1f Hz",
            approx_label,
            kind_label,
            passband_hz,
        )

        def _worker() -> None:
            try:
                designer = FilterDesigner(inputs)
                result = designer.design()
                self.after(0, lambda: self._on_design_done(designer, inputs, result))
            except Exception as exc:
                err = str(exc)
                logger.exception("SOFIA design failed")
                self.after(0, lambda: self._on_design_error(err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_design_done(self, designer, inputs, result) -> None:
        """Update all result widgets after a successful SOFIA design.

        Called in the main thread via ``after(0, ...)``.

        Args:
            designer: ``FilterDesigner`` that produced ``result``.
            inputs: ``DesignInputs`` used for synthesis.
            result: Completed ``DesignResult`` from SOFIA.
        """
        self._current_designer = designer
        self._current_result = result

        # ── Design info ─────────────────────────────────────────────────
        stage_lines = "\n".join(
            f"  Stage {i + 1}: {_format_stage(s)}" for i, s in enumerate(result.stages)
        )
        warning_text = (
            "\nWarnings: " + "; ".join(result.warnings) if result.warnings else ""
        )
        self._info_label.configure(
            text=(
                f"Order: {result.order}  ·  Stages: {len(result.stages)}  ·  "
                f"Poles: {len(result.poles)}\n"
                + stage_lines
                + warning_text
            ),
            text_color=theme_manager.get_color("text_secondary"),
        )

        # ── Measurement setup ────────────────────────────────────────────
        setup = designer.measurement_setup(result)
        notes_text = "\n".join(f"  • {n}" for n in setup.notes)
        self._meas_label.configure(
            text=(
                f"Sweep: {setup.start_hz:.2g} Hz → {setup.stop_hz:.2g} Hz\n"
                f"Points: {setup.num_points}  ·  Excitation: {setup.excitation_v:.3f} V\n"
                + notes_text
            ),
            text_color=theme_manager.get_color("text_secondary"),
        )

        # ── Netlist ──────────────────────────────────────────────────────
        try:
            netlist = designer.render_netlist(result)
            self._netlist = netlist
            self._netlist_text.configure(state="normal")
            self._netlist_text.delete("1.0", "end")
            self._netlist_text.insert("1.0", netlist)
            self._netlist_text.configure(state="disabled")
            self._copy_btn.configure(state="normal")
        except Exception as exc:
            logger.warning("Netlist rendering failed: %s", exc)

        # ── Bode plot ────────────────────────────────────────────────────
        try:
            freqs = np.logspace(
                np.log10(max(setup.start_hz, 1.0)),
                np.log10(setup.stop_hz),
                300,
            )
            validator = FilterValidator(result, inputs)
            mag_db, phase_deg = validator.theoretical_response(freqs)
            self._update_bode_plot(freqs, mag_db, phase_deg)
        except Exception as exc:
            logger.warning("Bode plot generation failed: %s", exc)

        self._set_status(
            f"Design complete: order {result.order}, {len(result.stages)} stage(s).",
            success=True,
        )
        self._design_btn.configure(state="normal", text="Design Filter")
        logger.info(
            "FiltersPanel: design done — order=%d stages=%d",
            result.order,
            len(result.stages),
        )

    def _on_design_error(self, message: str) -> None:
        """Display an error after SOFIA synthesis failure.

        Args:
            message: Human-readable error string.
        """
        self._set_status(f"Design failed: {message}", error=True)
        self._design_btn.configure(state="normal", text="Design Filter")

    def _on_copy_netlist(self) -> None:
        """Copy the SPICE netlist to the system clipboard."""
        if not self._netlist:
            return
        self.clipboard_clear()
        self.clipboard_append(self._netlist)
        self._copy_btn.configure(text="Copied!")
        self.after(1500, lambda: self._copy_btn.configure(text="Copy"))
        logger.debug("FiltersPanel: netlist copied to clipboard")

    # ------------------------------------------------------------------
    # Bode plot rendering
    # ------------------------------------------------------------------

    def _update_bode_plot(
        self,
        frequencies_hz: np.ndarray,
        magnitude_db: np.ndarray,
        phase_deg: np.ndarray,
    ) -> None:
        """Render a two-subplot Bode plot inside the plot card.

        Destroys any previously rendered canvas before drawing so memory
        is not leaked between successive designs.

        Args:
            frequencies_hz: Log-spaced frequency axis in hertz.
            magnitude_db: Magnitude response in decibels.
            phase_deg: Phase response in degrees.
        """
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            logger.warning("matplotlib backend unavailable; Bode plot skipped")
            return

        colors = theme_manager.get_colors()

        if self._bode_canvas_obj is not None:
            try:
                self._bode_canvas_obj.get_tk_widget().destroy()
            except Exception:
                pass
            self._bode_canvas_obj = None

        self._bode_placeholder.grid_forget()

        chart_bg = colors["chart_bg"]
        grid_col = colors["chart_grid"]
        text_col = colors["text_secondary"]
        accent = colors["accent_primary"]
        secondary = colors["accent_secondary"]
        warn_col = colors["warning"]

        fig = Figure(figsize=(5.5, 3.8), dpi=88, facecolor=chart_bg)
        ax_mag = fig.add_subplot(2, 1, 1)
        ax_phase = fig.add_subplot(2, 1, 2, sharex=ax_mag)

        for ax in (ax_mag, ax_phase):
            ax.set_facecolor(chart_bg)
            ax.tick_params(colors=text_col, labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor(grid_col)
            ax.grid(True, color=grid_col, linewidth=0.5, linestyle="--")

        ax_mag.semilogx(frequencies_hz, magnitude_db, color=accent, linewidth=1.5)
        ax_mag.axhline(-3.0, color=warn_col, linewidth=0.8, linestyle=":", label="−3 dB")
        ax_mag.set_ylabel("Magnitude (dB)", color=text_col, fontsize=8)
        ax_mag.legend(
            fontsize=7,
            facecolor=chart_bg,
            edgecolor=grid_col,
            labelcolor=text_col,
        )

        ax_phase.semilogx(frequencies_hz, phase_deg, color=secondary, linewidth=1.5)
        ax_phase.set_ylabel("Phase (°)", color=text_col, fontsize=8)
        ax_phase.set_xlabel("Frequency (Hz)", color=text_col, fontsize=8)
        ax_phase.tick_params(axis="x", colors=text_col)

        fig.tight_layout(pad=1.2)

        canvas = FigureCanvasTkAgg(fig, master=self._plot_card)
        canvas.draw()
        canvas.get_tk_widget().grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        self._bode_canvas_obj = canvas

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _set_status(
        self,
        message: str,
        *,
        error: bool = False,
        success: bool = False,
    ) -> None:
        """Update the status label below the form.

        Args:
            message: Text to display.
            error: Use error color when True.
            success: Use success color when True (ignored if ``error`` is set).
        """
        colors = theme_manager.get_colors()
        if error:
            color = colors["error"]
        elif success:
            color = colors["success"]
        else:
            color = colors["text_muted"]
        self._status_label.configure(text=message, text_color=color)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_body"):
            self._body.configure(fg_color=colors["bg_primary"])
        for card in (
            getattr(self, "_form_card", None),
            getattr(self, "_plot_card", None),
            getattr(self, "_info_card", None),
            getattr(self, "_meas_card", None),
            getattr(self, "_netlist_card", None),
        ):
            if card is not None:
                card.configure(fg_color=colors["bg_card"])
