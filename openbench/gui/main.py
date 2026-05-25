"""CustomTkinter application entrypoint.

Defines the root ``OpenBenchApp`` window and the ``main()`` CLI entry point.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> int:
    """Launch the OpenBench GUI application.

    Returns:
        Process exit code (0 on clean exit, 1 on fatal error).
    """
    try:
        import customtkinter as ctk  # noqa: PLC0415
    except ImportError:
        logger.error("customtkinter is required to run the GUI. Install it with: pip install customtkinter")
        return 1

    from openbench.gui.app import OpenBenchApp  # noqa: PLC0415

    logger.info("Starting OpenBench GUI")
    app = OpenBenchApp()
    app.mainloop()
    logger.info("OpenBench GUI exited")
    return 0
