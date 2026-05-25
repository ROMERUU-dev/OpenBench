"""Session history panel for reviewing recorded measurement sessions."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import customtkinter as ctk

from openbench.core.session import MeasurementSession, SessionManager, SessionState
from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

_STATE_COLORS: dict[str, str] = {
    SessionState.RUNNING: "#F59E0B",
    SessionState.COMPLETED: "#10B981",
    SessionState.FAILED: "#EF4444",
    SessionState.ABORTED: "#6B7280",
    SessionState.CREATED: "#60A5FA",
}

_STATE_LABELS: dict[str, str] = {
    SessionState.RUNNING: "Running",
    SessionState.COMPLETED: "Done",
    SessionState.FAILED: "Failed",
    SessionState.ABORTED: "Aborted",
    SessionState.CREATED: "Created",
}

_FILTER_STATES: list[str] = ["All", "Completed", "Running", "Failed", "Aborted"]


def _fmt_duration(seconds: float | None) -> str:
    """Format a duration in seconds as a human-readable string.

    Args:
        seconds: Duration in seconds, or ``None``.

    Returns:
        Formatted string such as ``"1m 23s"`` or ``"—"`` when unknown.
    """
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _fmt_datetime(ts: Any) -> str:
    """Format a datetime object as a local-ish display string.

    Args:
        ts: ``datetime`` object or ``None``.

    Returns:
        Formatted ``YYYY-MM-DD HH:MM`` string or ``"—"``.
    """
    if ts is None:
        return "—"
    try:
        return ts.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _fmt_size(size_bytes: int) -> str:
    """Format a byte count as a compact human-readable string.

    Args:
        size_bytes: File size in bytes.

    Returns:
        Formatted string such as ``"4.2 KB"`` or ``"1.1 MB"``.
    """
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


class SessionHistoryPanel(ContentPanel):
    """Browse and inspect recorded OpenBench measurement sessions.

    Two-column layout:

    - **Left**: scrollable session list with name search and state filter.
    - **Right**: detail view for the selected session — summary, instruments,
      events timeline, and artifact list.

    Sessions are loaded from the ``openbench_sessions`` root directory using
    :class:`~openbench.core.session.SessionManager`. The list is refreshed
    each time the panel becomes visible via ``on_show``.

    Args:
        master: Parent CustomTkinter widget.
        sessions_root: Optional override for the session root directory.
        **kwargs: Forwarded to :class:`~openbench.gui.panels.content_panel.ContentPanel`.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        *,
        sessions_root: Path | str | None = None,
        **kwargs: Any,
    ) -> None:
        self._sessions_root = Path(sessions_root) if sessions_root else None
        self._sessions: list[MeasurementSession] = []
        self._selected_session: MeasurementSession | None = None
        self._session_cards: dict[str, ctk.CTkFrame] = {}
        self._filter_state: str = "All"
        self._search_text: str = ""
        super().__init__(master, **kwargs)

    # ------------------------------------------------------------------
    # ContentPanel lifecycle
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        """Refresh the session list each time this panel becomes active."""
        super().on_show()
        self._load_sessions()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        # ── Header ──────────────────────────────────────────────────────
        self._build_header(colors, ff)

        # ── Body: two-column split ───────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        self._body = body

        self._build_list_column(body, colors, ff)
        self._build_detail_column(body, colors, ff)

    def _build_header(self, colors: dict, ff: str) -> None:
        """Build the top header bar with title and refresh button.

        Args:
            colors: Current theme color palette.
            ff: Font family string.
        """
        header = ctk.CTkFrame(self, fg_color="transparent", height=72)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)
        self._header_frame = header

        ctk.CTkLabel(
            header,
            text="Session History",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self._session_count_label = ctk.CTkLabel(
            header,
            text="Loading sessions…",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._session_count_label.grid(row=1, column=0, sticky="w")

        self._refresh_btn = ctk.CTkButton(
            header,
            text="↺  Refresh",
            width=96,
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self._load_sessions,
        )
        self._refresh_btn.grid(row=0, column=1, rowspan=2, sticky="e")

    def _build_list_column(self, parent: ctk.CTkFrame, colors: dict, ff: str) -> None:
        """Build the left-side session list column.

        Args:
            parent: Two-column body frame.
            colors: Current theme color palette.
            ff: Font family string.
        """
        list_col = ctk.CTkFrame(parent, fg_color=colors["bg_card"], corner_radius=0, width=280)
        list_col.grid(row=0, column=0, sticky="nsew", padx=(24, 0), pady=(16, 24))
        list_col.grid_propagate(False)
        list_col.rowconfigure(2, weight=1)
        list_col.columnconfigure(0, weight=1)
        self._list_col = list_col

        # Search box
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        ctk.CTkEntry(
            list_col,
            textvariable=self._search_var,
            placeholder_text="Search sessions…",
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            border_width=0,
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        # State filter tabs
        filter_bar = ctk.CTkFrame(list_col, fg_color="transparent")
        filter_bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._filter_btns: dict[str, ctk.CTkButton] = {}
        for i, label in enumerate(_FILTER_STATES):
            btn = ctk.CTkButton(
                filter_bar,
                text=label,
                width=48,
                height=22,
                corner_radius=6,
                fg_color=colors["accent_primary"] if label == "All" else colors["bg_input"],
                hover_color=colors["accent_hover"],
                text_color=colors["text_on_accent"] if label == "All" else colors["text_secondary"],
                font=(ff, 10),
                command=lambda lbl=label: self._on_filter_changed(lbl),
            )
            btn.grid(row=0, column=i, padx=(0, 4))
            self._filter_btns[label] = btn

        # Scrollable session cards
        self._session_scroll = ctk.CTkScrollableFrame(
            list_col,
            fg_color="transparent",
            label_text="",
            corner_radius=0,
        )
        self._session_scroll.grid(row=2, column=0, sticky="nsew")
        self._session_scroll.columnconfigure(0, weight=1)

        # Empty state label
        self._empty_label = ctk.CTkLabel(
            self._session_scroll,
            text="No sessions found.\nRun an experiment to create one.",
            font=(ff, 12),
            text_color=colors["text_muted"],
            justify="center",
        )

    def _build_detail_column(self, parent: ctk.CTkFrame, colors: dict, ff: str) -> None:
        """Build the right-side session detail column.

        Args:
            parent: Two-column body frame.
            colors: Current theme color palette.
            ff: Font family string.
        """
        detail_col = ctk.CTkScrollableFrame(
            parent,
            fg_color=colors["bg_primary"],
            label_text="",
            corner_radius=0,
        )
        detail_col.grid(row=0, column=1, sticky="nsew", padx=16, pady=(16, 24))
        detail_col.columnconfigure(0, weight=1)
        self._detail_col = detail_col

        # Placeholder shown when nothing is selected
        self._detail_placeholder = ctk.CTkLabel(
            detail_col,
            text="Select a session to view details.",
            font=(ff, 13),
            text_color=colors["text_muted"],
            anchor="center",
        )
        self._detail_placeholder.grid(row=0, column=0, pady=60)

        # Container for actual detail widgets (hidden until session selected)
        self._detail_container = ctk.CTkFrame(detail_col, fg_color="transparent")
        self._detail_container.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _scan_sessions(self) -> list[MeasurementSession]:
        """Scan the sessions directory and return loaded sessions.

        Returns:
            List of sessions ordered newest-first. Corrupt manifests are
            skipped with a warning. Returns ``[]`` on any filesystem error.
        """
        try:
            root = self._sessions_root or Path("openbench_sessions")
            manager = SessionManager(root_dir=root)
            paths = manager.list_sessions()
            loaded: list[MeasurementSession] = []
            for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    loaded.append(MeasurementSession.from_manifest(path))
                except Exception as exc:
                    logger.warning("Skipping corrupt session %s: %s", path.name, exc)
            logger.info(
                "SessionHistoryPanel: loaded %d sessions from %s", len(loaded), root
            )
            return loaded
        except Exception as exc:
            logger.error("SessionHistoryPanel: failed to load sessions: %s", exc)
            return []

    def _load_sessions(self) -> None:
        """Scan the sessions directory and refresh the session list."""
        self._sessions = self._scan_sessions()
        self._rebuild_session_list()
        self._update_count_label()

    def _rebuild_session_list(self) -> None:
        """Destroy and recreate session cards matching the current filter."""
        for widget in self._session_scroll.winfo_children():
            widget.destroy()
        self._session_cards.clear()

        visible = self._filtered_sessions()
        if not visible:
            self._empty_label = ctk.CTkLabel(
                self._session_scroll,
                text="No sessions found.\nRun an experiment to create one.",
                font=(str(theme_manager.get_font("family_fallback")), 12),
                text_color=theme_manager.get_color("text_muted"),
                justify="center",
            )
            self._empty_label.grid(row=0, column=0, pady=40)
            return

        for i, session in enumerate(visible):
            sid = session.session_id or str(i)
            card = self._make_session_card(self._session_scroll, session)
            card.grid(row=i, column=0, sticky="ew", padx=8, pady=(0, 6))
            self._session_cards[sid] = card

    def _filtered_sessions(self) -> list[MeasurementSession]:
        """Return sessions matching the current search text and state filter.

        Returns:
            Filtered, ordered list of sessions.
        """
        query = self._search_text.lower()
        result = []
        for session in self._sessions:
            if self._filter_state != "All":
                if session.state.lower() != self._filter_state.lower():
                    continue
            if query and query not in session.name.lower():
                continue
            result.append(session)
        return result

    def _update_count_label(self) -> None:
        """Update the subtitle label with total and visible session counts."""
        total = len(self._sessions)
        visible = len(self._filtered_sessions())
        if total == 0:
            text = "No sessions recorded yet"
        elif visible == total:
            text = f"{total} session{'s' if total != 1 else ''} found"
        else:
            text = f"Showing {visible} of {total} session{'s' if total != 1 else ''}"
        self._session_count_label.configure(text=text)

    # ------------------------------------------------------------------
    # Session card widget
    # ------------------------------------------------------------------

    def _make_session_card(
        self,
        master: ctk.CTkScrollableFrame,
        session: MeasurementSession,
    ) -> ctk.CTkFrame:
        """Build a compact session summary card widget.

        Args:
            master: Scrollable container for the card.
            session: Session to represent.

        Returns:
            Card frame widget.
        """
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))
        state_color = _STATE_COLORS.get(session.state, colors["text_muted"])
        state_label = _STATE_LABELS.get(session.state, str(session.state))

        card = ctk.CTkFrame(master, fg_color=colors["bg_card"], corner_radius=10)
        card.columnconfigure(0, weight=1)

        # Name row
        name_row = ctk.CTkFrame(card, fg_color="transparent")
        name_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        name_row.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            name_row,
            text=session.name,
            font=(ff, 12, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            name_row,
            text=state_label,
            font=(ff, 10, "bold"),
            text_color=state_color,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))

        # Meta row: date · duration · artifacts
        artifact_count = len(session.artifacts)
        meta = (
            f"{_fmt_datetime(session.started_at or session.created_at)}"
            f"  ·  {_fmt_duration(session.duration_s)}"
            f"  ·  {artifact_count} artifact{'s' if artifact_count != 1 else ''}"
        )
        if session.simulate:
            meta += "  ·  SIM"

        ctk.CTkLabel(
            card,
            text=meta,
            font=(ff, 10),
            text_color=colors["text_muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))

        # Tags (if any)
        if session.tags:
            ctk.CTkLabel(
                card,
                text="  ".join(f"#{t}" for t in session.tags),
                font=(ff, 9),
                text_color=colors["accent_secondary"],
                anchor="w",
            ).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 8))

        # Click anywhere on card to select
        sid = session.session_id
        for widget in card.winfo_children():
            widget.bind("<Button-1>", lambda _e, s=session: self._select_session(s))
        card.bind("<Button-1>", lambda _e, s=session: self._select_session(s))

        return card

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------

    def _select_session(self, session: MeasurementSession) -> None:
        """Display full detail for the selected session.

        Args:
            session: Session to show in the detail column.
        """
        self._selected_session = session
        self._highlight_selected_card(session.session_id)
        self._populate_detail(session)
        logger.debug("SessionHistoryPanel: selected %r", session.session_id)

    def _highlight_selected_card(self, session_id: str | None) -> None:
        """Update card background colors to reflect the active selection.

        Args:
            session_id: ID of the session that should appear highlighted.
        """
        colors = theme_manager.get_colors()
        for sid, card in self._session_cards.items():
            selected = sid == session_id
            card.configure(fg_color=colors["sidebar_active"] if selected else colors["bg_card"])

    def _populate_detail(self, session: MeasurementSession) -> None:
        """Rebuild the detail column widgets for the given session.

        Args:
            session: Fully loaded session to display.
        """
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))
        fm = str(theme_manager.get_font("family_mono"))

        # Hide placeholder, show container
        self._detail_placeholder.grid_remove()
        for widget in self._detail_container.winfo_children():
            widget.destroy()
        self._detail_container.grid(row=0, column=0, sticky="nsew")

        row = 0

        # ── Summary card ────────────────────────────────────────────────
        row = self._add_detail_card(
            row,
            title="Summary",
            content_builder=lambda parent, r: self._build_summary_content(
                parent, r, session, colors, ff
            ),
            colors=colors,
            ff=ff,
        )

        # ── Instruments card ────────────────────────────────────────────
        if session.instruments:
            row = self._add_detail_card(
                row,
                title=f"Instruments  ({len(session.instruments)})",
                content_builder=lambda parent, r: self._build_instruments_content(
                    parent, r, session, colors, ff
                ),
                colors=colors,
                ff=ff,
            )

        # ── Events timeline card ─────────────────────────────────────────
        if session.events:
            row = self._add_detail_card(
                row,
                title=f"Events  ({len(session.events)})",
                content_builder=lambda parent, r: self._build_events_content(
                    parent, r, session, colors, ff, fm
                ),
                colors=colors,
                ff=ff,
            )

        # ── Artifacts card ───────────────────────────────────────────────
        if session.artifacts:
            row = self._add_detail_card(
                row,
                title=f"Artifacts  ({len(session.artifacts)})",
                content_builder=lambda parent, r: self._build_artifacts_content(
                    parent, r, session, colors, ff
                ),
                colors=colors,
                ff=ff,
            )

        # ── Actions card ─────────────────────────────────────────────────
        self._build_actions_card(row, session, colors, ff)

    def _add_detail_card(
        self,
        grid_row: int,
        *,
        title: str,
        content_builder: Any,
        colors: dict,
        ff: str,
    ) -> int:
        """Add a titled card to the detail column and return the next grid row.

        Args:
            grid_row: Grid row inside the detail container.
            title: Card section title.
            content_builder: Callable ``(parent, start_row) -> next_row``.
            colors: Current theme color palette.
            ff: Font family string.

        Returns:
            Next available grid row after the card.
        """
        card = ctk.CTkFrame(
            self._detail_container, fg_color=colors["bg_card"], corner_radius=14
        )
        card.grid(row=grid_row, column=0, sticky="ew", pady=(0, 10))
        card.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text=title,
            font=(ff, 13, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        content_builder(card, 1)
        return grid_row + 1

    def _build_summary_content(
        self,
        parent: ctk.CTkFrame,
        start_row: int,
        session: MeasurementSession,
        colors: dict,
        ff: str,
    ) -> None:
        """Populate the summary card with key-value session metadata.

        Args:
            parent: Card frame.
            start_row: First grid row inside the card.
            session: Session whose metadata to display.
            colors: Current theme color palette.
            ff: Font family string.
        """
        state_color = _STATE_COLORS.get(session.state, colors["text_muted"])
        rows = [
            ("State", _STATE_LABELS.get(session.state, str(session.state)), state_color),
            ("Session ID", session.session_id or "—", None),
            ("Created", _fmt_datetime(session.created_at), None),
            ("Started", _fmt_datetime(session.started_at), None),
            ("Ended", _fmt_datetime(session.ended_at), None),
            ("Duration", _fmt_duration(session.duration_s), None),
            ("Simulate", "Yes" if session.simulate else "No", None),
            ("Tags", ", ".join(session.tags) if session.tags else "—", None),
        ]
        if session.metadata:
            for k, v in list(session.metadata.items())[:6]:
                rows.append((str(k), str(v)[:80], None))

        for i, (key, val, val_color) in enumerate(rows):
            r = start_row + i
            ctk.CTkLabel(
                parent,
                text=key,
                font=(ff, 11),
                text_color=colors["text_muted"],
                anchor="w",
                width=110,
            ).grid(row=r, column=0, sticky="w", padx=(16, 4), pady=2)
            ctk.CTkLabel(
                parent,
                text=val,
                font=(ff, 11),
                text_color=val_color or colors["text_secondary"],
                anchor="w",
            ).grid(row=r, column=1, sticky="w", padx=(0, 16), pady=2)

        # ensure two-column stretch
        parent.columnconfigure(1, weight=1)
        # bottom padding
        ctk.CTkFrame(parent, height=10, fg_color="transparent").grid(
            row=start_row + len(rows), column=0
        )

    def _build_instruments_content(
        self,
        parent: ctk.CTkFrame,
        start_row: int,
        session: MeasurementSession,
        colors: dict,
        ff: str,
    ) -> None:
        """Populate the instruments card with per-instrument rows.

        Args:
            parent: Card frame.
            start_row: First grid row inside the card.
            session: Session whose instruments to list.
            colors: Current theme color palette.
            ff: Font family string.
        """
        parent.columnconfigure(0, weight=1)
        for i, inst in enumerate(session.instruments):
            name = inst.get("name", "—")
            cls = inst.get("class", "")
            sim = " (sim)" if inst.get("simulate") else ""
            resource = inst.get("resource") or ""
            subtitle_parts = [p for p in [cls.split(".")[-1], resource, sim.strip()] if p]

            ctk.CTkLabel(
                parent,
                text=f"▪  {name}",
                font=(ff, 11, "bold"),
                text_color=colors["text_secondary"],
                anchor="w",
            ).grid(row=start_row + i * 2, column=0, sticky="w", padx=16, pady=(4, 0))
            ctk.CTkLabel(
                parent,
                text="  ".join(subtitle_parts) if subtitle_parts else "—",
                font=(ff, 10),
                text_color=colors["text_muted"],
                anchor="w",
            ).grid(row=start_row + i * 2 + 1, column=0, sticky="w", padx=24, pady=(0, 4))

        ctk.CTkFrame(parent, height=8, fg_color="transparent").grid(
            row=start_row + len(session.instruments) * 2, column=0
        )

    def _build_events_content(
        self,
        parent: ctk.CTkFrame,
        start_row: int,
        session: MeasurementSession,
        colors: dict,
        ff: str,
        fm: str,
    ) -> None:
        """Populate the events card with a compact timeline.

        Args:
            parent: Card frame.
            start_row: First grid row inside the card.
            session: Session whose events to show.
            colors: Current theme color palette.
            ff: Font family string.
            fm: Monospace font family string.
        """
        parent.columnconfigure(0, weight=1)
        max_events = 20
        events = session.events[-max_events:]
        for i, event in enumerate(events):
            ts = _fmt_datetime(event.timestamp)
            ctk.CTkLabel(
                parent,
                text=f"  {ts}  {event.name}",
                font=(fm, 10),
                text_color=colors["text_secondary"],
                anchor="w",
            ).grid(row=start_row + i, column=0, sticky="w", padx=16, pady=1)

        if len(session.events) > max_events:
            ctk.CTkLabel(
                parent,
                text=f"  … {len(session.events) - max_events} older events omitted",
                font=(ff, 10),
                text_color=colors["text_muted"],
                anchor="w",
            ).grid(row=start_row + len(events), column=0, sticky="w", padx=16, pady=(2, 0))

        ctk.CTkFrame(parent, height=8, fg_color="transparent").grid(
            row=start_row + len(events) + 1, column=0
        )

    def _build_artifacts_content(
        self,
        parent: ctk.CTkFrame,
        start_row: int,
        session: MeasurementSession,
        colors: dict,
        ff: str,
    ) -> None:
        """Populate the artifacts card listing file outputs.

        Args:
            parent: Card frame.
            start_row: First grid row inside the card.
            session: Session whose artifacts to list.
            colors: Current theme color palette.
            ff: Font family string.
        """
        parent.columnconfigure(1, weight=1)
        for i, artifact in enumerate(session.artifacts):
            r = start_row + i
            ctk.CTkLabel(
                parent,
                text=artifact.kind,
                font=(ff, 10),
                text_color=colors["accent_secondary"],
                anchor="w",
                width=120,
            ).grid(row=r, column=0, sticky="w", padx=(16, 4), pady=2)
            ctk.CTkLabel(
                parent,
                text=f"{artifact.name}  ({_fmt_size(artifact.size_bytes)})",
                font=(ff, 10),
                text_color=colors["text_secondary"],
                anchor="w",
            ).grid(row=r, column=1, sticky="w", padx=(0, 16), pady=2)

        ctk.CTkFrame(parent, height=8, fg_color="transparent").grid(
            row=start_row + len(session.artifacts), column=0
        )

    def _build_actions_card(
        self,
        grid_row: int,
        session: MeasurementSession,
        colors: dict,
        ff: str,
    ) -> None:
        """Add a card with open-folder and copy-ID action buttons.

        Args:
            grid_row: Grid row inside the detail container.
            session: Selected session.
            colors: Current theme color palette.
            ff: Font family string.
        """
        card = ctk.CTkFrame(
            self._detail_container, fg_color=colors["bg_card"], corner_radius=14
        )
        card.grid(row=grid_row, column=0, sticky="ew", pady=(0, 10))
        card.columnconfigure(0, weight=1)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew", padx=16, pady=12)

        session_path = session.path
        open_state = "normal" if (session_path and session_path.exists()) else "disabled"

        ctk.CTkButton(
            btn_row,
            text="Open Folder",
            width=110,
            height=30,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            state=open_state,
            command=lambda p=session_path: self._open_folder(p),
        ).grid(row=0, column=0, padx=(0, 8))

        self._copy_id_btn = ctk.CTkButton(
            btn_row,
            text="Copy ID",
            width=80,
            height=30,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=lambda sid=session.session_id: self._copy_to_clipboard(sid or ""),
        )
        self._copy_id_btn.grid(row=0, column=1)

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------

    def _on_search_changed(self, *_: Any) -> None:
        """Rebuild the list when the search text changes."""
        self._search_text = self._search_var.get()
        self._rebuild_session_list()
        self._update_count_label()

    def _on_filter_changed(self, label: str) -> None:
        """Update the active state filter and rebuild the session list.

        Args:
            label: Filter label clicked (``"All"``, ``"Completed"``, etc.).
        """
        colors = theme_manager.get_colors()
        self._filter_state = label
        for lbl, btn in self._filter_btns.items():
            active = lbl == label
            btn.configure(
                fg_color=colors["accent_primary"] if active else colors["bg_input"],
                text_color=(
                    colors["text_on_accent"] if active else colors["text_secondary"]
                ),
            )
        self._rebuild_session_list()
        self._update_count_label()

    def _open_folder(self, path: Path | None) -> None:
        """Open the session directory in the system file manager.

        Args:
            path: Session directory path.
        """
        if path is None or not path.exists():
            logger.warning("SessionHistoryPanel: session folder not found: %s", path)
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
            logger.info("SessionHistoryPanel: opened folder %s", path)
        except Exception as exc:
            logger.error("SessionHistoryPanel: cannot open folder: %s", exc)

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to the system clipboard.

        Args:
            text: String to copy.
        """
        self.clipboard_clear()
        self.clipboard_append(text)
        if hasattr(self, "_copy_id_btn"):
            self._copy_id_btn.configure(text="Copied!")
            self.after(1500, lambda: self._copy_id_btn.configure(text="Copy ID"))
        logger.debug("SessionHistoryPanel: copied to clipboard")

    # ------------------------------------------------------------------
    # Theme refresh
    # ------------------------------------------------------------------

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_body"):
            self._body.configure(fg_color="transparent")
        if hasattr(self, "_list_col"):
            self._list_col.configure(fg_color=colors["bg_card"])
        if hasattr(self, "_detail_col"):
            self._detail_col.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_filter_btns"):
            for lbl, btn in self._filter_btns.items():
                active = lbl == self._filter_state
                btn.configure(
                    fg_color=colors["accent_primary"] if active else colors["bg_input"],
                    text_color=(
                        colors["text_on_accent"] if active else colors["text_secondary"]
                    ),
                )
