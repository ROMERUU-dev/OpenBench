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


@dataclass(frozen=True)
class WaveformConfig:
    """Function generator output waveform configuration.

    Attributes:
        waveform: Waveform shape (``"sine"``, ``"square"``, ``"triangle"``,
            ``"ramp"``, ``"dc"``).
        frequency_hz: Output frequency in hertz.
        amplitude_v: Peak-to-peak amplitude in volts.
        offset_v: DC offset in volts.
        phase_deg: Phase offset in degrees.
        duty_cycle: Duty cycle fraction 0–1, applicable to square and ramp
            waveforms.
        channel: Output channel identifier. Defaults to 1.
    """

    waveform: str
    frequency_hz: float
    amplitude_v: float
    offset_v: float = 0.0
    phase_deg: float = 0.0
    duty_cycle: float = 0.5
    channel: InstrumentChannel = 1


@dataclass(frozen=True)
class FrequencySweepPoint:
    """Single-frequency result from a function generator or lock-in sweep.

    Attributes:
        frequency_hz: Stimulus frequency in hertz.
        channel: Source channel identifier.
    """

    frequency_hz: float
    channel: InstrumentChannel = 1


class IFunctionGenerator(IInstrument, ABC):
    """Abstract interface for programmable function / signal generators.

    Backends wrap their existing driver libraries (e.g. the VirtualBench
    function generator driver) and expose this uniform interface so
    experiments are portable across hardware.
    """

    @abstractmethod
    def configure(self, config: WaveformConfig) -> None:
        """Apply a waveform configuration to the output channel.

        Args:
            config: Complete waveform specification to apply.

        Raises:
            ValueError: If any parameter is outside backend limits.
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def enable_output(self, channel: InstrumentChannel = 1, *, enabled: bool = True) -> None:
        """Enable or disable the signal output for a channel.

        Args:
            channel: Backend-specific channel identifier.
            enabled: ``True`` to enable, ``False`` to disable the output.

        Raises:
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def sweep(
        self,
        channel: InstrumentChannel,
        start_hz: float,
        stop_hz: float,
        num_points: int,
        amplitude_v: float,
        *,
        log_scale: bool = True,
        dwell_s: float = 0.1,
        waveform: str = "sine",
    ) -> list[FrequencySweepPoint]:
        """Sweep the output frequency across a range.

        Args:
            channel: Backend-specific output channel identifier.
            start_hz: Starting frequency in hertz.
            stop_hz: Ending frequency in hertz.
            num_points: Number of frequency steps including endpoints.
            amplitude_v: Peak-to-peak amplitude in volts for all steps.
            log_scale: When ``True``, steps are distributed on a log scale.
            dwell_s: Settling time in seconds at each frequency step.
            waveform: Waveform shape applied at each step.

        Returns:
            Ordered list of frequency setpoints applied.

        Raises:
            ValueError: If sweep parameters are invalid or outside backend
                limits.
            Exception: Propagates backend communication failures.
        """


@dataclass(frozen=True)
class OscilloscopeReading:
    """Time-domain waveform acquired from an oscilloscope channel.

    Attributes:
        channel: Source channel identifier.
        time_s: Sample timestamps in seconds relative to trigger.
        voltage_v: Sampled voltage values in volts, parallel to ``time_s``.
        sample_rate_hz: Effective sample rate of the acquisition in hertz.
        metadata: Optional backend-specific details (trigger mode, coupling,
            impedance, or raw driver payload).
    """

    channel: InstrumentChannel
    time_s: list[float]
    voltage_v: list[float]
    sample_rate_hz: float
    metadata: dict[str, Any] = field(default_factory=dict)


class IOscilloscope(IInstrument, ABC):
    """Abstract interface for oscilloscopes.

    Backends wrap their existing drivers (VirtualBench, Rigol DS1000E,
    Tektronix TBS1000C) and implement this contract so experiments can
    acquire waveforms from any supported oscilloscope.
    """

    @abstractmethod
    def configure_channel(
        self,
        channel: InstrumentChannel,
        *,
        volts_per_div: float,
        coupling: str = "DC",
        enabled: bool = True,
    ) -> None:
        """Configure vertical settings for one oscilloscope channel.

        Args:
            channel: Backend-specific channel identifier.
            volts_per_div: Vertical scale in volts per division.
            coupling: Input coupling mode — ``"DC"``, ``"AC"``, or ``"GND"``.
            enabled: ``True`` to display this channel, ``False`` to disable it.

        Raises:
            ValueError: If parameters are outside backend limits.
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def configure_timebase(
        self,
        time_per_div_s: float,
        *,
        trigger_level_v: float = 0.0,
        trigger_channel: InstrumentChannel = 1,
        trigger_slope: str = "rising",
    ) -> None:
        """Configure horizontal timebase and trigger settings.

        Args:
            time_per_div_s: Horizontal scale in seconds per division.
            trigger_level_v: Trigger threshold voltage in volts.
            trigger_channel: Channel used as the trigger source.
            trigger_slope: ``"rising"`` or ``"falling"`` edge trigger.

        Raises:
            ValueError: If parameters are outside backend limits.
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def acquire(self, channel: InstrumentChannel) -> OscilloscopeReading:
        """Acquire a single-shot waveform from the specified channel.

        The backend arms the trigger, waits for the acquisition to complete,
        and transfers the waveform to the host.  Simulated backends return a
        synthetic waveform without hardware access.

        Args:
            channel: Backend-specific channel identifier.

        Returns:
            Acquired time-domain waveform with timestamps and voltage samples.

        Raises:
            TimeoutError: If the trigger does not fire within the backend
                timeout.
            Exception: Propagates backend communication failures.
        """


@dataclass(frozen=True)
class ImpedancePoint:
    """Single-frequency impedance measurement result.

    Attributes:
        frequency_hz: Stimulus frequency in hertz.
        z_real_ohm: Real part of impedance in ohms (resistance).
        z_imag_ohm: Imaginary part of impedance in ohms (reactance).
        phase_deg: Impedance phase angle in degrees.
        magnitude_ohm: Impedance magnitude ``|Z|`` in ohms.
        metadata: Optional backend-specific details such as excitation
            amplitude, averaging, or lock-in time constant.
    """

    frequency_hz: float
    z_real_ohm: float
    z_imag_ohm: float
    phase_deg: float
    magnitude_ohm: float
    metadata: dict[str, Any] = field(default_factory=dict)


class IImpedanceAnalyzer(IInstrument, ABC):
    """Abstract interface for impedance analyzers and lock-in amplifiers.

    Backends wrap drivers for instruments such as the Stanford Research SR860
    lock-in amplifier configured for impedance measurements. All SI units.
    """

    @abstractmethod
    def measure_at_freq(
        self,
        frequency_hz: float,
        *,
        excitation_v: float | None = None,
        settle_periods: int = 5,
    ) -> ImpedancePoint:
        """Measure impedance at a single stimulus frequency.

        Args:
            frequency_hz: Stimulus frequency in hertz.
            excitation_v: Optional excitation voltage amplitude in volts. When
                ``None``, the backend keeps its current excitation level.
            settle_periods: Minimum number of excitation periods to wait for
                the lock-in to settle before sampling.

        Returns:
            Impedance measurement result at the requested frequency.

        Raises:
            ValueError: If the frequency is outside the backend's range.
            Exception: Propagates backend communication failures.
        """

    @abstractmethod
    def sweep(
        self,
        start_hz: float,
        stop_hz: float,
        num_points: int,
        *,
        excitation_v: float | None = None,
        log_scale: bool = True,
        settle_periods: int = 5,
    ) -> list[ImpedancePoint]:
        """Sweep the stimulus frequency and measure impedance at each point.

        Args:
            start_hz: Starting stimulus frequency in hertz.
            stop_hz: Ending stimulus frequency in hertz.
            num_points: Number of frequency points including endpoints.
            excitation_v: Optional excitation voltage amplitude in volts.
            log_scale: When ``True``, frequency points are distributed on a
                log10 scale.
            settle_periods: Minimum lock-in settle periods per frequency step.

        Returns:
            Ordered impedance measurements for each stimulus frequency.

        Raises:
            ValueError: If sweep parameters are invalid or outside backend
                limits.
            Exception: Propagates backend communication failures.
        """


__all__ = [
    "DCSweepPoint",
    "DCSweepReading",
    "FrequencySweepPoint",
    "IDCSupply",
    "IFunctionGenerator",
    "IImpedanceAnalyzer",
    "IInstrument",
    "IOscilloscope",
    "ImpedancePoint",
    "InstrumentChannel",
    "InstrumentStatus",
    "OscilloscopeReading",
    "WaveformConfig",
]
