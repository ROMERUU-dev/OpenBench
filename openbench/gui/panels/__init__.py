"""CustomTkinter panels for OpenBench workflows."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from openbench.gui.panels.header import HeaderBar
    from openbench.gui.panels.sidebar import SidebarPanel

    __all__ = [
        "HeaderBar",
        "SidebarPanel",
        "logger",
    ]
except ImportError:
    logger.debug("GUI panels unavailable (customtkinter not installed)")
    __all__ = ["logger"]
