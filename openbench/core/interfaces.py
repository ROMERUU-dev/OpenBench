"""Abstract interfaces shared by OpenBench instrument adapters."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType

logger = logging.getLogger(__name__)


class InstrumentStatus(StrEnum):
    """Connection state reported by an OpenBench instrument adapter."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    SIMULATED = "simulated"
    ERROR = "error"


@dataclass
class IInstrument(ABC):
    """Base class for OpenBench instrument adapters.

    The base class owns the public connection lifecycle so concrete backends can
    focus on wrapping their existing driver libraries. Subclasses implement only
    the private connection hooks and may keep their standalone backend behavior
    behind those hooks.

    Attributes:
        name: Human-readable instrument name used by the orchestrator registry.
        resource: Optional backend-specific resource identifier, such as a VISA
            address, serial path, hostname, or local simulator label.
        simulate: When True, marks the adapter as simulated without opening
            hardware resources.
    """

    name: str
    resource: str | None = None
    simulate: bool = False
    _status: InstrumentStatus = field(default=InstrumentStatus.DISCONNECTED, init=False)
    _last_error: Exception | None = field(default=None, init=False, repr=False)

    def connect(self) -> None:
        """Connect to the underlying instrument or backend.

        The operation is idempotent. Simulated instruments transition directly
        to ``InstrumentStatus.SIMULATED`` so experiments can run without
        hardware during development.

        Raises:
            Exception: Re-raises any backend exception from ``_connect`` after
                recording the error state.
        """

        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._status = InstrumentStatus.SIMULATED
            logger.info("Instrument using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("Instrument connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("Instrument connected: %s", self.name)

    def disconnect(self) -> None:
        """Release the underlying instrument or backend connection.

        The operation is idempotent and clears simulated connections without
        calling the backend hook.

        Raises:
            Exception: Re-raises any backend exception from ``_disconnect`` after
                recording the error state.
        """

        if self._status == InstrumentStatus.DISCONNECTED:
            logger.debug("Instrument already disconnected: %s", self.name)
            return

        logger.info("Disconnecting instrument: %s", self.name)

        if self._status == InstrumentStatus.SIMULATED:
            self._status = InstrumentStatus.DISCONNECTED
            logger.info("Simulated instrument disconnected: %s", self.name)
            return

        try:
            self._disconnect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("Instrument disconnect failed: %s", self.name)
            raise

        self._status = InstrumentStatus.DISCONNECTED
        logger.info("Instrument disconnected: %s", self.name)

    def status(self) -> InstrumentStatus:
        """Return the current instrument connection status.

        Returns:
            Current adapter status.
        """

        return self._status

    def last_error(self) -> Exception | None:
        """Return the last backend lifecycle exception.

        Returns:
            Last connection or disconnection exception, or None when no lifecycle
            error has been recorded.
        """

        return self._last_error

    def __enter__(self) -> IInstrument:
        """Connect and return the instrument for context-manager usage.

        Returns:
            Connected instrument instance.
        """

        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Disconnect the instrument when leaving a context manager.

        Args:
            exc_type: Exception type raised inside the context, when present.
            exc_value: Exception raised inside the context, when present.
            traceback: Traceback raised inside the context, when present.
        """

        self.disconnect()

    @abstractmethod
    def _connect(self) -> None:
        """Open backend-specific hardware or library resources."""

    @abstractmethod
    def _disconnect(self) -> None:
        """Close backend-specific hardware or library resources."""
