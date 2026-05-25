"""Abstract interfaces shared by OpenBench instrument adapters."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)
InstrumentChannel = str | int


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

    def __post_init__(self) -> None:
        """Subclass initialization hook called after the dataclass ``__init__``.

        Override in concrete adapters to initialize backend-specific state
        without re-defining ``__init__``.  The base implementation is a no-op.
        """

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


@dataclass(frozen=True)
class DCSweepPoint:
    """Single DC supply sweep setpoint.

    Attributes:
        channel: Backend-specific output channel identifier, commonly 1, 2, 3,
            "CH1", or a named rail.
        voltage_v: Voltage setpoint in volts.
        current_limit_a: Optional current compliance limit in amperes. When
            None, the backend keeps the channel current limit unchanged.
        dwell_s: Settling time in seconds after applying this setpoint.
    """

    channel: InstrumentChannel
    voltage_v: float
    current_limit_a: float | None = None
    dwell_s: float = 0.0


@dataclass(frozen=True)
class DCSweepReading:
    """Measured or simulated result for one DC supply sweep point.

    Attributes:
        channel: Backend-specific output channel identifier.
        voltage_setpoint_v: Requested voltage setpoint in volts.
        current_limit_a: Current compliance limit in amperes used for the point,
            when known.
        measured_voltage_v: Measured output voltage in volts, when the backend
            can report it.
        measured_current_a: Measured output current in amperes, when the backend
            can report it.
        metadata: Optional backend-specific details such as protection state,
            range, timestamp, or raw driver payload.
    """

    channel: InstrumentChannel
    voltage_setpoint_v: float
    current_limit_a: float | None = None
    measured_voltage_v: float | None = None
    measured_current_a: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class IDCSupply(IInstrument, ABC):
    """Abstract interface for programmable DC power supplies.

    Backends implement this contract by wrapping their existing driver library
    instead of reimplementing instrument communication. All values use SI units
    so experiments can coordinate Keysight, VirtualBench, and simulation
    backends without driver-specific conversions in experiment code.
    """

    @abstractmethod
    def set_voltage(self, channel: InstrumentChannel, voltage_v: float) -> None:
        """Set the output voltage for a DC supply channel.

        Args:
            channel: Backend-specific output channel identifier.
            voltage_v: Voltage setpoint in volts.

        Raises:
            ValueError: If the channel or voltage is outside backend limits.
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def set_current(self, channel: InstrumentChannel, current_a: float) -> None:
        """Set the current compliance limit for a DC supply channel.

        Args:
            channel: Backend-specific output channel identifier.
            current_a: Current limit in amperes.

        Raises:
            ValueError: If the channel or current is outside backend limits.
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def sweep(
        self,
        channel: InstrumentChannel,
        start_v: float,
        stop_v: float,
        step_v: float,
        *,
        current_limit_a: float | None = None,
        dwell_s: float = 0.0,
    ) -> list[DCSweepReading]:
        """Sweep a DC supply channel across voltage setpoints.

        Args:
            channel: Backend-specific output channel identifier.
            start_v: First voltage setpoint in volts.
            stop_v: Final voltage boundary in volts.
            step_v: Voltage step in volts. The sign determines sweep direction.
            current_limit_a: Optional current compliance limit in amperes to
                apply before or during the sweep.
            dwell_s: Settling time in seconds after each setpoint.

        Returns:
            Ordered readings for each applied setpoint. Simulated backends may
            mirror setpoints into measured values.

        Raises:
            ValueError: If sweep parameters are invalid or outside backend
                limits.
            Exception: Propagates backend communication failures.
        """


__all__ = [
    "DCSweepPoint",
    "DCSweepReading",
    "IDCSupply",
    "IInstrument",
    "InstrumentChannel",
    "InstrumentStatus",
]
