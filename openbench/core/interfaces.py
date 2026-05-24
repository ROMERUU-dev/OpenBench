"""Abstract interfaces shared by OpenBench instrument adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class InstrumentStatus(StrEnum):
    """Connection state reported by an OpenBench instrument adapter."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    SIMULATED = "simulated"
    ERROR = "error"


class IInstrument(Protocol):
    """Base protocol implemented by all instrument adapters."""

    name: str

    def connect(self) -> None:
        """Connect to the underlying instrument or backend."""

    def disconnect(self) -> None:
        """Release the underlying instrument or backend connection."""

    def status(self) -> InstrumentStatus:
        """Return the current instrument connection status.

        Returns:
            Current adapter status.
        """
