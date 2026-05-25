"""CustomTkinter GUI package for OpenBench."""

from __future__ import annotations

import logging

from openbench.gui.theme import FONTS, PALETTE, ThemeManager, ThemeMode, theme_manager

logger = logging.getLogger(__name__)

try:
    from openbench.gui.app import OpenBenchApp

    __all__ = [
        "OpenBenchApp",
        "ThemeManager",
        "ThemeMode",
        "PALETTE",
        "FONTS",
        "theme_manager",
        "logger",
    ]
except ImportError:
    logger.debug("OpenBenchApp unavailable (customtkinter not installed)")
    __all__ = [
        "ThemeManager",
        "ThemeMode",
        "PALETTE",
        "FONTS",
        "theme_manager",
        "logger",
    ]
