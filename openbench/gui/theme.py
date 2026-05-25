"""CustomTkinter theme manager for OpenBench.

Provides a singleton ThemeManager that controls dark/light appearance,
exposes a design-token color palette, and allows widgets to subscribe
to theme-change events without coupling to the CTk internals.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)

_THEME_JSON = Path(__file__).parent / "assets" / "openbench_theme.json"

ThemeMode = Literal["dark", "light", "system"]
EffectiveMode = Literal["dark", "light"]

# Design-token color palette keyed by effective mode.
PALETTE: dict[str, dict[str, str]] = {
    "dark": {
        "bg_primary": "#1a1b2e",
        "bg_secondary": "#16213e",
        "bg_card": "#0f3460",
        "bg_input": "#1e293b",
        "accent_primary": "#00d4ff",
        "accent_secondary": "#7c3aed",
        "accent_hover": "#38bdf8",
        "text_primary": "#e2e8f0",
        "text_secondary": "#94a3b8",
        "text_muted": "#64748b",
        "text_on_accent": "#0f172a",
        "success": "#10b981",
        "warning": "#f59e0b",
        "error": "#ef4444",
        "border": "#334155",
        "border_focus": "#00d4ff",
        "sidebar_bg": "#0f172a",
        "sidebar_active": "#1e3a5f",
        "chart_bg": "#0d1117",
        "chart_grid": "#21262d",
    },
    "light": {
        "bg_primary": "#f8fafc",
        "bg_secondary": "#f1f5f9",
        "bg_card": "#ffffff",
        "bg_input": "#ffffff",
        "accent_primary": "#0284c7",
        "accent_secondary": "#7c3aed",
        "accent_hover": "#0369a1",
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_muted": "#94a3b8",
        "text_on_accent": "#ffffff",
        "success": "#059669",
        "warning": "#d97706",
        "error": "#dc2626",
        "border": "#e2e8f0",
        "border_focus": "#0284c7",
        "sidebar_bg": "#1e293b",
        "sidebar_active": "#334155",
        "chart_bg": "#f8fafc",
        "chart_grid": "#e2e8f0",
    },
}

# Spacing tokens (pixels) — xs→3xl covers all layout gaps and padding.
SPACING: dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "base": 16,
    "lg": 24,
    "xl": 32,
    "2xl": 48,
    "3xl": 64,
}

# Border-radius tokens (pixels) — matches the CTK corner_radius values in the theme JSON.
RADIUS: dict[str, int] = {
    "none": 0,
    "sm": 4,
    "md": 8,
    "lg": 12,
    "xl": 16,
    "2xl": 20,
    "full": 1000,
}

# Typography tokens.
FONTS: dict[str, str | int] = {
    "family_primary": "Inter",
    "family_fallback": "Segoe UI",
    "family_mono": "JetBrains Mono",
    "family_mono_fallback": "Courier New",
    "size_xs": 10,
    "size_sm": 12,
    "size_base": 13,
    "size_md": 14,
    "size_lg": 16,
    "size_xl": 20,
    "size_2xl": 24,
    "size_3xl": 30,
    "weight_normal": "normal",
    "weight_bold": "bold",
}


class ThemeManager:
    """Application-wide theme controller for the OpenBench GUI.

    Manages dark/light/system appearance mode for CustomTkinter, exposes a
    typed color-token palette, and dispatches change events to subscribed
    callbacks. Designed to be used as the module-level singleton
    ``theme_manager``.

    Attributes:
        _mode: Requested mode including the "system" sentinel.
        _effective: Resolved effective mode ("dark" or "light").
        _callbacks: Registered theme-change listeners.
        _ctk_ready: True once CustomTkinter has been successfully configured.
    """

    def __init__(self) -> None:
        self._mode: ThemeMode = "system"
        self._effective: EffectiveMode = "dark"
        self._callbacks: list[Callable[[EffectiveMode], None]] = []
        self._ctk_ready: bool = False
        self._try_configure_ctk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_theme(self, mode: ThemeMode) -> None:
        """Switch to the given theme mode and notify listeners.

        Args:
            mode: One of ``"dark"``, ``"light"``, or ``"system"``.
        """
        self._mode = mode
        previous = self._effective
        self._effective = self._resolve(mode)
        self._apply_ctk()
        if self._effective != previous:
            self._dispatch()
        logger.info("Theme set to %s (effective: %s)", mode, self._effective)

    def get_mode(self) -> ThemeMode:
        """Return the requested mode (may be ``"system"``).

        Returns:
            The ThemeMode string.
        """
        return self._mode

    def get_effective_mode(self) -> EffectiveMode:
        """Return the resolved display mode, never ``"system"``.

        Returns:
            Either ``"dark"`` or ``"light"``.
        """
        return self._effective

    def toggle(self) -> None:
        """Toggle between ``"dark"`` and ``"light"`` modes."""
        target: ThemeMode = "light" if self._effective == "dark" else "dark"
        self.set_theme(target)

    def get_colors(self) -> dict[str, str]:
        """Return the full color palette for the active mode.

        Returns:
            Mapping of token name → hex color string.
        """
        return PALETTE[self._effective]

    def get_color(self, key: str) -> str:
        """Return a single color token for the active mode.

        Args:
            key: Token name such as ``"accent_primary"`` or ``"bg_card"``.

        Returns:
            Hex color string, or ``"#000000"`` if the key is unknown.
        """
        return PALETTE[self._effective].get(key, "#000000")

    def get_font(self, key: str) -> str | int:
        """Return a typography token.

        Args:
            key: Token name such as ``"size_base"`` or ``"family_primary"``.

        Returns:
            Font value (string or int), or empty string if key is unknown.
        """
        return FONTS.get(key, "")

    def get_spacing(self, key: str) -> int:
        """Return a spacing token in pixels.

        Args:
            key: Token name such as ``"base"``, ``"sm"``, or ``"xl"``.

        Returns:
            Integer pixel value, or 0 if the key is unknown.
        """
        return SPACING.get(key, 0)

    def get_radius(self, key: str) -> int:
        """Return a border-radius token in pixels.

        Args:
            key: Token name such as ``"md"``, ``"lg"``, or ``"full"``.

        Returns:
            Integer pixel value, or 0 if the key is unknown.
        """
        return RADIUS.get(key, 0)

    def on_theme_change(self, callback: Callable[[EffectiveMode], None]) -> None:
        """Register a callback invoked when the effective mode changes.

        Args:
            callback: Callable receiving the new effective mode string.
        """
        self._callbacks.append(callback)
        logger.debug("Registered theme-change callback: %s", callback)

    def remove_theme_callback(self, callback: Callable[[EffectiveMode], None]) -> None:
        """Remove a previously registered theme-change callback.

        Args:
            callback: The exact callable object that was registered.
        """
        try:
            self._callbacks.remove(callback)
        except ValueError:
            logger.debug("Callback not found for removal: %s", callback)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_configure_ctk(self) -> None:
        """Initialize CustomTkinter with the OpenBench appearance and color theme."""
        try:
            import customtkinter as ctk  # noqa: PLC0415

            self._effective = self._resolve(self._mode)
            ctk.set_appearance_mode(self._effective)
            if _THEME_JSON.exists():
                ctk.set_default_color_theme(str(_THEME_JSON))
                logger.debug("Loaded OpenBench CTK theme from %s", _THEME_JSON)
            else:
                ctk.set_default_color_theme("blue")
                logger.warning("Theme JSON not found at %s; falling back to 'blue'", _THEME_JSON)
            self._ctk_ready = True
            logger.debug("CustomTkinter configured (mode=%s)", self._effective)
        except ImportError:
            logger.warning("customtkinter not installed; ThemeManager runs headless")
        except Exception as exc:
            logger.warning("CustomTkinter init failed: %s", exc)

    def _resolve(self, mode: ThemeMode) -> EffectiveMode:
        """Resolve ``"system"`` to a concrete dark/light value.

        Args:
            mode: Possibly ``"system"``.

        Returns:
            ``"dark"`` or ``"light"``.
        """
        if mode != "system":
            return mode  # type: ignore[return-value]
        return self._detect_system_mode()

    def _detect_system_mode(self) -> EffectiveMode:
        """Ask the OS for the preferred appearance mode.

        Returns:
            ``"dark"`` or ``"light"`` (defaults to ``"dark"`` on failure).
        """
        try:
            import darkdetect  # noqa: PLC0415

            result = darkdetect.theme()
            if result and result.lower() == "light":
                return "light"
        except Exception as exc:
            logger.debug("darkdetect query failed: %s", exc)
        return "dark"

    def _apply_ctk(self) -> None:
        """Push the current effective mode to CustomTkinter if available."""
        if not self._ctk_ready:
            return
        try:
            import customtkinter as ctk  # noqa: PLC0415

            ctk.set_appearance_mode(self._effective)
            logger.debug("CTk appearance mode updated to %s", self._effective)
        except Exception as exc:
            logger.warning("Failed to update CTk appearance mode: %s", exc)

    def _dispatch(self) -> None:
        """Invoke all registered theme-change callbacks."""
        for cb in list(self._callbacks):
            try:
                cb(self._effective)
            except Exception as exc:
                logger.warning("Theme callback %s raised: %s", cb, exc)


# Module-level singleton used throughout the application.
theme_manager = ThemeManager()

__all__ = [
    "ThemeManager",
    "ThemeMode",
    "EffectiveMode",
    "PALETTE",
    "FONTS",
    "SPACING",
    "RADIUS",
    "theme_manager",
]
