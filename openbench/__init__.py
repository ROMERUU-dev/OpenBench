"""OpenBench lab instrument orchestration platform."""

from __future__ import annotations

import logging
from importlib import metadata

logger = logging.getLogger(__name__)

try:
    __version__ = metadata.version("openbench")
except metadata.PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__", "logger"]
