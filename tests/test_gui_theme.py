"""Tests for the GUI ThemeManager and palette constants."""

from __future__ import annotations

from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from openbench.gui.theme import FONTS, PALETTE, ThemeManager


# ---------------------------------------------------------------------------
# Palette structure
# ---------------------------------------------------------------------------


def test_palette_has_dark_and_light() -> None:
    assert "dark" in PALETTE
    assert "light" in PALETTE


def test_palette_tokens_match_between_modes() -> None:
    """Both modes must expose the same set of color tokens."""
    assert set(PALETTE["dark"].keys()) == set(PALETTE["light"].keys())


def test_palette_all_values_are_hex_strings() -> None:
    for mode, colors in PALETTE.items():
        for key, value in colors.items():
            assert isinstance(value, str), f"{mode}.{key} is not a string"
            assert value.startswith("#"), f"{mode}.{key} does not start with '#'"
            assert len(value) == 7, f"{mode}.{key} is not a 6-digit hex color"


def test_fonts_has_expected_keys() -> None:
    required = {"family_primary", "family_fallback", "family_mono", "size_base", "size_lg"}
    assert required.issubset(set(FONTS.keys()))


# ---------------------------------------------------------------------------
# ThemeManager construction
# ---------------------------------------------------------------------------


def _make_manager() -> ThemeManager:
    """Create a ThemeManager with CTk mocked out so no display is needed."""
    with patch("openbench.gui.theme.ThemeManager._try_configure_ctk"):
        mgr = ThemeManager()
    mgr._ctk_ready = False  # ensure headless
    return mgr


def test_default_mode_is_system() -> None:
    mgr = _make_manager()
    assert mgr.get_mode() == "system"


def test_set_theme_dark() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    assert mgr.get_effective_mode() == "dark"
    assert mgr.get_mode() == "dark"


def test_set_theme_light() -> None:
    mgr = _make_manager()
    mgr.set_theme("light")
    assert mgr.get_effective_mode() == "light"


def test_toggle_switches_mode() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    mgr.toggle()
    assert mgr.get_effective_mode() == "light"
    mgr.toggle()
    assert mgr.get_effective_mode() == "dark"


# ---------------------------------------------------------------------------
# Color / font accessors
# ---------------------------------------------------------------------------


def test_get_colors_returns_palette_for_mode() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    assert mgr.get_colors() == PALETTE["dark"]
    mgr.set_theme("light")
    assert mgr.get_colors() == PALETTE["light"]


def test_get_color_known_key() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    assert mgr.get_color("accent_primary") == PALETTE["dark"]["accent_primary"]


def test_get_color_unknown_key_returns_black() -> None:
    mgr = _make_manager()
    assert mgr.get_color("nonexistent_token_xyz") == "#000000"


def test_get_font_known_key() -> None:
    mgr = _make_manager()
    assert mgr.get_font("size_base") == FONTS["size_base"]


def test_get_font_unknown_key_returns_empty() -> None:
    mgr = _make_manager()
    assert mgr.get_font("nonexistent_font_token") == ""


# ---------------------------------------------------------------------------
# Callback system
# ---------------------------------------------------------------------------


def test_callback_fired_on_mode_change() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    cb = MagicMock()
    mgr.on_theme_change(cb)
    mgr.toggle()  # dark → light
    cb.assert_called_once_with("light")


def test_callback_not_fired_when_mode_unchanged() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    cb = MagicMock()
    mgr.on_theme_change(cb)
    mgr.set_theme("dark")  # same mode, no change
    cb.assert_not_called()


def test_multiple_callbacks_all_fired() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    cb1, cb2 = MagicMock(), MagicMock()
    mgr.on_theme_change(cb1)
    mgr.on_theme_change(cb2)
    mgr.toggle()
    cb1.assert_called_once_with("light")
    cb2.assert_called_once_with("light")


def test_remove_callback() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")
    cb = MagicMock()
    mgr.on_theme_change(cb)
    mgr.remove_theme_callback(cb)
    mgr.toggle()
    cb.assert_not_called()


def test_remove_nonexistent_callback_does_not_raise() -> None:
    mgr = _make_manager()
    cb = MagicMock()
    mgr.remove_theme_callback(cb)  # should be silent


def test_callback_exception_does_not_propagate() -> None:
    mgr = _make_manager()
    mgr.set_theme("dark")

    def bad_cb(mode: Literal["dark", "light"]) -> None:
        raise RuntimeError("oops")

    mgr.on_theme_change(bad_cb)
    mgr.toggle()  # must not raise


# ---------------------------------------------------------------------------
# System mode resolution
# ---------------------------------------------------------------------------


def test_system_mode_resolved_via_darkdetect_dark() -> None:
    mgr = _make_manager()
    with patch("darkdetect.theme", return_value="Dark"):
        mgr.set_theme("system")
    assert mgr.get_effective_mode() == "dark"


def test_system_mode_resolved_via_darkdetect_light() -> None:
    mgr = _make_manager()
    with patch("darkdetect.theme", return_value="Light"):
        mgr.set_theme("system")
    assert mgr.get_effective_mode() == "light"


def test_system_mode_defaults_to_dark_on_failure() -> None:
    mgr = _make_manager()
    with patch("darkdetect.theme", side_effect=Exception("no OS")):
        mgr.set_theme("system")
    assert mgr.get_effective_mode() == "dark"
