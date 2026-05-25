"""Reusable CustomTkinter widgets for OpenBench."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

try:
    from openbench.gui.widgets.status_bar import StatusBar
    from openbench.gui.widgets.theme_toggle import ThemeToggleButton

    __all__ = [
        "StatusBar",
        "ThemeToggleButton",
        "logger",
    ]
except ImportError:
    logger.debug("GUI widgets unavailable (customtkinter not installed)")
    __all__ = ["logger"]
