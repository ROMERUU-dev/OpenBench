"""Rigol DS1000E-series oscilloscope backend adapter for OpenBench.

Wraps the standalone ``rigol_ds1000e_python`` project (~/rigol_ds1000e_python)
without reimplementing USBTMC communication.  The ``RigolDS1102E`` and
``UsbTmc`` classes are imported lazily the first time a connection is opened so
OpenBench can be imported on machines where the library is absent.

Simulation mode provides a ``_StubRigolScope`` that generates synthetic
waveforms, keeping experiment code usable without hardware.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openbench.core.interfaces import (
    IOscilloscope,
    InstrumentChannel,
    InstrumentStatus,
    OscilloscopeReading,
)

logger = logging.getLogger(__name__)

_LIB_ROOT = Path.home() / "rigol_ds1000e_python"

# Channel identifier constants used by the Rigol SCPI layer
_CHANNEL_MAP: dict[Any, str] = {
    1: "CHANnel1",
    "1": "CHANnel1",
    "CH1": "CHANnel1",
    "CHANNEL1": "CHANnel1",
    "CHANnel1": "CHANnel1",
    2: "CHANnel2",
    "2": "CHANnel2",
    "CH2": "CHANnel2",
    "CHANNEL2": "CHANnel2",
    "CHANnel2": "CHANnel2",
}

# Waveform conversion constants (matching the library's read_waveform formula)
_WAVEFORM_REFERENCE = 240.0
_WAVEFORM_SCALE_DIV = 25.0
_WAVEFORM_OFFSET_BIAS = 4.6
_SCREEN_DIVS = 12


# ---------------------------------------------------------------------------
# Library import helpers
# ---------------------------------------------------------------------------


def _ensure_lib_on_path() -> bool:
    """Insert the rigol library root into ``sys.path`` when present.

    Returns:
        ``True`` when the directory exists and is on ``sys.path``.
    """
    lib = str(_LIB_ROOT)
    if lib in sys.path:
        return True
    if _LIB_ROOT.is_dir():
        sys.path.insert(0, lib)
        logger.debug("Added rigol_ds1000e_python to sys.path: %s", lib)
        return True
    logger.warning("rigol_ds1000e_python not found at %s", _LIB_ROOT)
    return False


def _import_rigol_scope() -> Any:
    """Import and return the ``RigolDS1102E`` class from the backend library.

    Raises:
        ImportError: When the library directory is absent or import fails.
    """
    if not _ensure_lib_on_path():
        raise ImportError(
            f"rigol_ds1000e_python not found at {_LIB_ROOT}. "
            "Clone the project there or set simulate=True."
        )
    from rigol_ds1102e_gui import RigolDS1102E  # type: ignore[import]

    return RigolDS1102E


def _import_usbtmc_error() -> Any:
    """Import and return the ``UsbTmcError`` class.

    Returns:
        ``UsbTmcError`` class or ``Exception`` as fallback.
    """
    try:
        from rigol_ds1102e_gui import UsbTmcError  # type: ignore[import]

        return UsbTmcError
    except Exception:
        return Exception


# ---------------------------------------------------------------------------
# Channel normalization
# ---------------------------------------------------------------------------


def _normalize_channel(channel: InstrumentChannel) -> str:
    """Convert an OpenBench channel identifier to a Rigol SCPI channel string.

    Args:
        channel: Integer (1 or 2), ``"CH1"``/``"CH2"``, ``"CHANnel1"``/
            ``"CHANnel2"``, or ``"CHANNEL1"``/``"CHANNEL2"``.

    Returns:
        Normalized SCPI string (``"CHANnel1"`` or ``"CHANnel2"``).

    Raises:
        ValueError: If the channel cannot be mapped to a valid identifier.
    """
    if isinstance(channel, int):
        key: Any = channel
    else:
        key = str(channel).strip()

    result = _CHANNEL_MAP.get(key)
    if result is None and isinstance(key, str):
        for k, v in _CHANNEL_MAP.items():
            if isinstance(k, str) and k.upper() == key.upper():
                return v

    if result is None:
        raise ValueError(
            f"Invalid Rigol channel {channel!r}; "
            "expected 1/2, 'CH1'/'CH2', or 'CHANnel1'/'CHANnel2'."
        )
    return result


# ---------------------------------------------------------------------------
# Stub implementation for simulation mode
# ---------------------------------------------------------------------------


class _StubRigolScope:
    """In-process stub that mimics ``RigolDS1102E`` without hardware.

    Generates synthetic sinusoidal waveforms for both channels so that
    experiment code can be exercised without a physical oscilloscope.
    """

    def __init__(self) -> None:
        self._ch_scale: dict[str, float] = {"CHANnel1": 1.0, "CHANnel2": 1.0}
        self._ch_offset: dict[str, float] = {"CHANnel1": 0.0, "CHANnel2": 0.0}
        self._ch_coupling: dict[str, str] = {"CHANnel1": "DC", "CHANnel2": "DC"}
        self._ch_enabled: dict[str, bool] = {"CHANnel1": True, "CHANnel2": True}
        self._time_scale: float = 1e-3
        self._time_offset: float = 0.0
        self._trig_source: str = "CHANnel1"
        self._trig_slope: str = "POSitive"
        self._trig_level: float = 0.0
        self._idn: str = "RIGOL TECHNOLOGIES,DS1102E,SIM000000,0.0"

    def idn(self) -> str:
        return self._idn

    def command(self, command: str) -> None:
        # Parse channel and timebase settings applied by configure_channel /
        # configure_timebase so queries return consistent values.
        cmd = command.strip()
        for ch in ("CHANnel1", "CHANnel2"):
            if cmd.startswith(f":{ch}:SCALe "):
                self._ch_scale[ch] = float(cmd.split()[-1])
            elif cmd.startswith(f":{ch}:OFFSet "):
                self._ch_offset[ch] = float(cmd.split()[-1])
            elif cmd.startswith(f":{ch}:COUPling "):
                self._ch_coupling[ch] = cmd.split()[-1]
            elif cmd.startswith(f":{ch}:DISPlay "):
                self._ch_enabled[ch] = cmd.split()[-1].upper() in {"1", "ON"}
        if cmd.startswith(":TIMebase:SCALe "):
            self._time_scale = float(cmd.split()[-1])
        elif cmd.startswith(":TIMebase:OFFSet "):
            self._time_offset = float(cmd.split()[-1])
        elif cmd.startswith(":TRIGger:EDGE:SOURce "):
            self._trig_source = cmd.split()[-1]
        elif cmd.startswith(":TRIGger:EDGE:SLOPe "):
            self._trig_slope = cmd.split()[-1]
        elif cmd.startswith(":TRIGger:EDGE:LEVel "):
            self._trig_level = float(cmd.split()[-1])

    def query(self, command: str, timeout: float | None = None) -> str:
        cmd = command.strip()
        for ch in ("CHANnel1", "CHANnel2"):
            if cmd == f":{ch}:SCALe?":
                return str(self._ch_scale[ch])
            if cmd == f":{ch}:OFFSet?":
                return str(self._ch_offset[ch])
            if cmd == f":{ch}:COUPling?":
                return self._ch_coupling[ch]
            if cmd == f":{ch}:DISPlay?":
                return "ON" if self._ch_enabled[ch] else "OFF"
        if cmd == ":TIMebase:SCALe?":
            return str(self._time_scale)
        if cmd == ":TIMebase:OFFSet?":
            return str(self._time_offset)
        return ""

    def read_waveform(self, channel: str, mode: str = "NORMal") -> Any:
        """Generate a synthetic waveform matching the library's ``Waveform`` API."""
        scale = self._ch_scale.get(channel, 1.0)
        offset = self._ch_offset.get(channel, 0.0)
        time_scale = self._time_scale
        time_offset = self._time_offset

        # 600 samples spread over 12 × time_scale seconds
        n = 600
        span = _SCREEN_DIVS * time_scale
        times = [((i / max(1, n - 1)) - 0.5) * span + time_offset for i in range(n)]

        amp_divs = 3.0 if channel == "CHANnel1" else 2.0
        freq = max(1.0 / span * 3, 100.0)
        phase = 0.0 if channel == "CHANnel1" else -math.pi / 4

        volts = [
            amp_divs * scale * math.sin(2 * math.pi * freq * t + phase) - offset
            for t in times
        ]
        # Reverse engineer raw bytes (library formula: volts = (240 - raw) * scale/25 - (offset + scale*4.6))
        raw = [
            int(max(0, min(255, round(_WAVEFORM_REFERENCE - (v + offset + scale * _WAVEFORM_OFFSET_BIAS) * _WAVEFORM_SCALE_DIV / scale))))
            for v in volts
        ]

        # Return an object with the same attributes as the library's Waveform
        return _WaveformData(
            channel=channel,
            times=times,
            volts=volts,
            raw=raw,
            scale=scale,
            offset=offset,
            time_scale=time_scale,
            time_offset=time_offset,
        )


@dataclass
class _WaveformData:
    """Lightweight substitute for the library's ``Waveform`` dataclass."""

    channel: str
    times: list[float]
    volts: list[float]
    raw: list[int]
    scale: float
    offset: float
    time_scale: float
    time_offset: float


# ---------------------------------------------------------------------------
# Main backend adapter
# ---------------------------------------------------------------------------


@dataclass
class RigolDS1000EBackend(IOscilloscope):
    """OpenBench ``IOscilloscope`` adapter for the Rigol DS1000E-series.

    Wraps ``RigolDS1102E`` from the standalone ``rigol_ds1000e_python``
    project.  The library communicates over the Linux USBTMC subsystem so no
    additional VISA installation is required.

    Simulation mode uses a ``_StubRigolScope`` so experiments can run during
    development without a connected oscilloscope.

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: Path to the USBTMC device node (e.g. ``"/dev/usbtmc0"``).
            Defaults to ``"/dev/usbtmc0"`` when ``None``.
        simulate: When ``True``, a synthetic stub scope is used instead of
            opening hardware.
        acquire_timeout_s: Maximum seconds to wait for the scope to respond
            during waveform acquisition.
    """

    acquire_timeout_s: float = 4.0
    _scope: Any = field(default=None, init=False, repr=False)
    _ch_configs: dict[str, dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    _timebase: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._ch_configs = {}
        self._timebase = {
            "time_per_div_s": 1e-3,
            "trigger_level_v": 0.0,
            "trigger_channel": 1,
            "trigger_slope": "rising",
        }

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open the USBTMC device and verify the oscilloscope identity."""
        RigolDS1102E = _import_rigol_scope()
        device = self.resource or "/dev/usbtmc0"
        self._scope = RigolDS1102E(device)
        idn = self._scope.idn()
        logger.info("Rigol IDN: %s", idn)

    def _disconnect(self) -> None:
        """Release the underlying USBTMC file descriptor."""
        # RigolDS1102E / UsbTmc opens and closes the fd per-command so there
        # is no persistent session to close — just clear the reference.
        self._scope = None

    def connect(self) -> None:  # type: ignore[override]
        """Connect to hardware or initialize the simulation stub.

        Overrides the base ``connect`` so that simulation mode populates
        ``_scope`` with a stub, keeping the other method implementations
        uniform regardless of mode.
        """
        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._scope = _StubRigolScope()
            self._status = InstrumentStatus.SIMULATED
            logger.info("Rigol adapter using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("Rigol connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("Rigol adapter connected: %s", self.name)

    # ------------------------------------------------------------------
    # IOscilloscope interface
    # ------------------------------------------------------------------

    def configure_channel(
        self,
        channel: InstrumentChannel,
        *,
        volts_per_div: float,
        coupling: str = "DC",
        enabled: bool = True,
    ) -> None:
        """Configure vertical settings for one oscilloscope channel.

        Sends ``:CHANnelN:SCALe``, ``:CHANnelN:COUPling``, and
        ``:CHANnelN:DISPlay`` SCPI commands to the scope.

        Args:
            channel: 1/2, ``"CH1"``/``"CH2"``, or ``"CHANnel1"``/``"CHANnel2"``.
            volts_per_div: Vertical scale in volts per division.
            coupling: Input coupling — ``"DC"``, ``"AC"``, or ``"GND"``.
            enabled: ``True`` to display the channel on screen.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the channel identifier is not valid.
        """
        self._require_scope()
        ch = _normalize_channel(channel)
        self._ch_configs[ch] = {
            "volts_per_div": volts_per_div,
            "coupling": coupling.upper(),
            "enabled": enabled,
        }
        logger.debug(
            "configure_channel %s: %.3g V/div, coupling=%s, enabled=%s",
            ch, volts_per_div, coupling, enabled,
        )

        display_token = "ON" if enabled else "OFF"
        self._scope.command(f":{ch}:DISPlay {display_token}")
        self._scope.command(f":{ch}:COUPling {coupling.upper()}")
        self._scope.command(f":{ch}:SCALe {volts_per_div!r}")
        self._scope.command(f":{ch}:OFFSet 0")

    def configure_timebase(
        self,
        time_per_div_s: float,
        *,
        trigger_level_v: float = 0.0,
        trigger_channel: InstrumentChannel = 1,
        trigger_slope: str = "rising",
    ) -> None:
        """Configure horizontal timebase and edge trigger settings.

        Sends ``:TIMebase:SCALe``, ``:TIMebase:OFFSet``, and
        ``:TRIGger:EDGE:*`` SCPI commands to the scope.

        Args:
            time_per_div_s: Horizontal scale in seconds per division.
            trigger_level_v: Trigger threshold voltage in volts.
            trigger_channel: Channel used as trigger source.
            trigger_slope: ``"rising"`` or ``"falling"`` edge.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the trigger channel is not valid.
        """
        self._require_scope()
        trig_ch = _normalize_channel(trigger_channel)
        slope_scpi = "POSitive" if trigger_slope.lower() == "rising" else "NEGative"

        self._timebase = {
            "time_per_div_s": time_per_div_s,
            "trigger_level_v": trigger_level_v,
            "trigger_channel": trigger_channel,
            "trigger_slope": trigger_slope,
        }
        logger.debug(
            "configure_timebase: %.3g s/div, trigger %.3g V (%s %s)",
            time_per_div_s, trigger_level_v, trig_ch, slope_scpi,
        )

        self._scope.command(f":TIMebase:SCALe {time_per_div_s!r}")
        self._scope.command(":TIMebase:OFFSet 0")
        self._scope.command(":TRIGger:MODE EDGE")
        self._scope.command(f":TRIGger:EDGE:SOURce {trig_ch}")
        self._scope.command(f":TRIGger:EDGE:SLOPe {slope_scpi}")
        self._scope.command(f":TRIGger:EDGE:LEVel {trigger_level_v!r}")

    def acquire(self, channel: InstrumentChannel) -> OscilloscopeReading:
        """Acquire a single-shot waveform from the specified channel.

        Arms the trigger, waits for the acquisition to complete, then
        transfers and converts the waveform data.  Simulation mode returns
        a synthetic waveform without hardware access.

        Args:
            channel: 1/2, ``"CH1"``/``"CH2"``, or ``"CHANnel1"``/``"CHANnel2"``.

        Returns:
            Acquired or simulated time-domain waveform.

        Raises:
            RuntimeError: If the adapter is not connected.
            TimeoutError: If the trigger does not fire within
                ``acquire_timeout_s``.
            ValueError: If the channel identifier is not valid.
        """
        self._require_scope()
        ch = _normalize_channel(channel)

        self._scope.command(":SINGle")
        self._scope.command(":RUN")

        if not self.simulate:
            self._wait_for_trigger()

        wave = self._scope.read_waveform(ch, "NORMal")

        if not wave.volts:
            raise RuntimeError(
                f"No waveform data returned for channel {channel!r}."
            )

        time_scale = wave.time_scale
        n = len(wave.times)
        sample_rate = (
            float(n) / (_SCREEN_DIVS * time_scale) if time_scale > 0 else 1.0
        )

        return OscilloscopeReading(
            channel=channel,
            time_s=list(wave.times),
            voltage_v=list(wave.volts),
            sample_rate_hz=sample_rate,
            metadata={
                "rigol_channel": ch,
                "scale_v_per_div": wave.scale,
                "offset_v": wave.offset,
                "time_scale_s_per_div": time_scale,
                "backend": "rigol_ds1000e" if not self.simulate else "rigol_sim",
                "num_samples": n,
            },
        )

    # ------------------------------------------------------------------
    # Extra Rigol-specific helpers
    # ------------------------------------------------------------------

    def identify(self) -> str:
        """Query and return the scope's ``*IDN?`` response.

        Returns:
            IDN string from the oscilloscope.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        return self._scope.idn()

    def autoscale(self) -> None:
        """Send ``:AUToscale`` to let the scope configure its own scales.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        logger.debug("Rigol autoscale triggered")
        self._scope.command(":AUToscale")

    def run(self) -> None:
        """Put the scope into continuous acquisition mode.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        self._scope.command(":RUN")

    def stop(self) -> None:
        """Halt ongoing acquisitions on the scope.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        self._scope.command(":STOP")

    def force_trigger(self) -> None:
        """Force a trigger event using ``:TFORce``.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        self._scope.command(":TFORce")

    def send_command(self, command: str) -> None:
        """Send an arbitrary SCPI command to the scope.

        Args:
            command: Raw SCPI command string.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        logger.debug("Rigol raw command: %s", command)
        self._scope.command(command)

    def query(self, command: str, *, timeout: float | None = None) -> str:
        """Send an arbitrary SCPI query and return the response string.

        Args:
            command: Raw SCPI query string ending with ``?``.
            timeout: Optional per-query read timeout in seconds.

        Returns:
            Decoded response string with surrounding whitespace stripped.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        logger.debug("Rigol raw query: %s", command)
        return self._scope.query(command, timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_scope(self) -> None:
        if self._scope is None:
            raise RuntimeError(
                f"Rigol adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )

    def _wait_for_trigger(self) -> None:
        """Poll the trigger status until it fires or the timeout elapses.

        Raises:
            TimeoutError: If the trigger has not fired within
                ``acquire_timeout_s``.
        """
        deadline = time.monotonic() + self.acquire_timeout_s
        while time.monotonic() < deadline:
            try:
                status = self._scope.query(":TRIGger:STATus?", timeout=1.0).strip().upper()
            except Exception:
                logger.debug("Trigger status query failed — retrying", exc_info=True)
                time.sleep(0.05)
                continue

            if status in {"STOP", "AUTO", "T'D"}:
                return
            time.sleep(0.05)

        raise TimeoutError(
            f"Rigol trigger did not fire within {self.acquire_timeout_s:.1f} s."
        )


__all__ = ["RigolDS1000EBackend"]
