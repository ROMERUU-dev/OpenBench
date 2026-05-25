"""Structural and unit tests for SessionHistoryPanel."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openbench.gui.panels.content_panel import ContentPanel


# ---------------------------------------------------------------------------
# Import / subclass checks (no widget instantiation)
# ---------------------------------------------------------------------------


def test_session_history_panel_importable() -> None:
    from openbench.gui.panels.session_history_panel import SessionHistoryPanel

    assert issubclass(SessionHistoryPanel, ContentPanel)


def test_session_history_panel_in_panels_init() -> None:
    from openbench.gui import panels

    assert hasattr(panels, "SessionHistoryPanel")


def test_session_history_panel_in_app_registry() -> None:
    from openbench.gui.app import _PANEL_REGISTRY
    from openbench.gui.panels.session_history_panel import SessionHistoryPanel

    assert "session_history" in _PANEL_REGISTRY
    assert _PANEL_REGISTRY["session_history"] is SessionHistoryPanel


def test_data_sessions_key_routes_to_session_history() -> None:
    from openbench.gui.app import _KEY_TO_GROUP

    assert _KEY_TO_GROUP.get("data_sessions") == "session_history"


# ---------------------------------------------------------------------------
# Helper function unit tests (no GUI)
# ---------------------------------------------------------------------------


def test_fmt_duration_none() -> None:
    from openbench.gui.panels.session_history_panel import _fmt_duration

    assert _fmt_duration(None) == "—"


def test_fmt_duration_seconds() -> None:
    from openbench.gui.panels.session_history_panel import _fmt_duration

    assert _fmt_duration(45) == "45s"


def test_fmt_duration_minutes() -> None:
    from openbench.gui.panels.session_history_panel import _fmt_duration

    assert _fmt_duration(90) == "1m 30s"


def test_fmt_datetime_none() -> None:
    from openbench.gui.panels.session_history_panel import _fmt_datetime

    assert _fmt_datetime(None) == "—"


def test_fmt_datetime_object() -> None:
    from openbench.gui.panels.session_history_panel import _fmt_datetime

    dt = datetime(2026, 5, 24, 14, 32, tzinfo=UTC)
    result = _fmt_datetime(dt)
    assert "2026-05-24" in result
    assert "14:32" in result


def test_fmt_size_bytes() -> None:
    from openbench.gui.panels.session_history_panel import _fmt_size

    assert _fmt_size(512) == "512 B"
    assert "KB" in _fmt_size(2048)
    assert "MB" in _fmt_size(2 * 1024 * 1024)


# ---------------------------------------------------------------------------
# SessionHistoryPanel filtering logic (no GUI, via internal method via session
# data loading from a temp directory)
# ---------------------------------------------------------------------------


def _write_minimal_manifest(session_dir: Path, name: str, state: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "session_id": session_dir.name,
        "name": name,
        "state": state,
        "simulate": False,
        "tags": [],
        "created_at": "2026-05-24T12:00:00+00:00",
        "started_at": "2026-05-24T12:00:01+00:00",
        "ended_at": "2026-05-24T12:00:10+00:00",
        "duration_s": 9.0,
        "metadata": {},
        "config": None,
        "environment": {},
        "instruments": [],
        "events": [],
        "artifacts": [],
    }
    (session_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _make_panel_stub(root: Path) -> object:
    """Construct a SessionHistoryPanel stub without calling __init__ (no GUI)."""
    from openbench.gui.panels.session_history_panel import SessionHistoryPanel

    panel = SessionHistoryPanel.__new__(SessionHistoryPanel)
    panel._sessions_root = root
    panel._sessions = []
    panel._session_cards = {}
    panel._filter_state = "All"
    panel._search_text = ""
    return panel


def test_scan_sessions_reads_manifests(tmp_path: Path) -> None:
    """_scan_sessions returns sessions loaded from disk."""
    root = tmp_path / "sessions"
    _write_minimal_manifest(root / "s1", "Alpha sweep", "completed")
    _write_minimal_manifest(root / "s2", "Beta sweep", "failed")

    panel = _make_panel_stub(root)
    sessions = panel._scan_sessions()  # type: ignore[attr-defined]

    assert len(sessions) == 2
    names = {s.name for s in sessions}
    assert "Alpha sweep" in names
    assert "Beta sweep" in names


def test_filtered_sessions_state_filter(tmp_path: Path) -> None:
    """_filtered_sessions respects the current state filter."""
    root = tmp_path / "sessions"
    _write_minimal_manifest(root / "s1", "Alpha", "completed")
    _write_minimal_manifest(root / "s2", "Beta", "failed")

    panel = _make_panel_stub(root)
    panel._sessions = panel._scan_sessions()  # type: ignore[attr-defined]
    panel._filter_state = "Completed"  # type: ignore[attr-defined]

    visible = panel._filtered_sessions()  # type: ignore[attr-defined]
    assert all(s.state == "completed" for s in visible)
    assert len(visible) == 1


def test_filtered_sessions_search_filter(tmp_path: Path) -> None:
    """_filtered_sessions respects the search text."""
    root = tmp_path / "sessions"
    _write_minimal_manifest(root / "s1", "Chua admittance", "completed")
    _write_minimal_manifest(root / "s2", "Inductor sweep", "completed")

    panel = _make_panel_stub(root)
    panel._sessions = panel._scan_sessions()  # type: ignore[attr-defined]
    panel._search_text = "chua"  # type: ignore[attr-defined]

    visible = panel._filtered_sessions()  # type: ignore[attr-defined]
    assert len(visible) == 1
    assert "chua" in visible[0].name.lower()


def test_scan_sessions_skips_corrupt_manifest(tmp_path: Path) -> None:
    """Corrupt manifests are skipped without raising."""
    root = tmp_path / "sessions"
    bad = root / "bad_session"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{invalid json", encoding="utf-8")

    panel = _make_panel_stub(root)
    sessions = panel._scan_sessions()  # type: ignore[attr-defined]

    assert sessions == []


def test_scan_sessions_empty_dir(tmp_path: Path) -> None:
    """Empty sessions root results in an empty session list."""
    root = tmp_path / "no_sessions_here"

    panel = _make_panel_stub(root)
    sessions = panel._scan_sessions()  # type: ignore[attr-defined]

    assert sessions == []
