"""Changelog documentation tests."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"


def test_changelog_documents_initial_setup_phase() -> None:
    """Verify the initial changelog documents the setup baseline."""

    changelog = CHANGELOG_PATH.read_text(encoding="utf-8")

    assert changelog.startswith("# Changelog")
    assert "## [Unreleased]" in changelog
    assert "Initial changelog created for phase `1-setup`." in changelog
    assert "## [0.1.0] - 2026-05-24" in changelog
    assert "OpenBench composes existing backend projects" in changelog
