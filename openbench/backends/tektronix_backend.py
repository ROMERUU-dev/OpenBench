"""Tektronix TBS1000C-series oscilloscope backend adapter for OpenBench.

Wraps the standalone ``tektronix-tbs1000c-linux`` project
(~/tektronix-tbs1000c-linux) without reimplementing USBTMC communication.
``UsbTmc`` and ``read_waveform`` are imported lazily at connection time so
OpenBench can be imported on machines where the library is absent.

Simulation mode provides a ``_StubTektronixScope`` that generates synthetic
waveforms, keeping experiment code usable without hardware or a display.
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

_LIB_ROOT = Path.home() / "tektronix-tbs1000c-linux"

_CHANNEL_VALID_STRINGS: frozenset[str] = frozenset(
    {"CH1", "CH2", "MATH", "REF1", "REF2"}
)

_SCREEN_DIVS = 10


# ---------------------------------------------------------------------------
# Library import helpers
# ---------------------------------------------------------------------------


def _ensure_lib_on_path() -> bool:
    """Insert the Tektronix library root into ``sys.path`` when present.

    Returns:
        ``True`` when the directory exists and is on ``sys.path``.
    """
    lib = str(_LIB_ROOT)
    if lib in sys.path:
        return True
    if _LIB_ROOT.is_dir():
        sys.path.insert(0, lib)
        logger.debug("Added tektronix-tbs1000c-linux to sys.path: %s", lib)
        return True
    logger.warning("tektronix-tbs1000c-linux not found at %s", _LIB_ROOT)
    return False


def _import_usbtmc() -> Any:
    """Import and return the ``UsbTmc`` class from the backend library.

    Raises:
        ImportError: When the library directory is absent or import fails.
    """
    if not _ensure_lib_on_path():
        raise ImportError(
            f"tektronix-tbs1000c-linux not found at {_LIB_ROOT}. "
            "Clone the project there or set simulate=True."
        )
    from tbs1000c_app import UsbTmc  # type: ignore[import]

    return UsbTmc


def _import_read_waveform() -> Any:
    """Import and return the ``read_waveform`` function from the backend library.

    Raises:
        ImportError: When the library directory is absent or import fails.
    """
    if not _ensure_lib_on_path():
        raise ImportError(
            f"tektronix-tbs1000c-linux not found at {_LIB_ROOT}. "
            "Clone the project there or set simulate=True."
        )
    from tbs1000c_app import read_waveform  # type: ignore[import]

    return read_waveform


# ---------------------------------------------------------------------------
# Channel normalization
# ---------------------------------------------------------------------------


def _normalize_channel(channel: InstrumentChannel) -> str:
    """Convert an OpenBench channel identifier to a Tektronix SCPI channel string.

    Args:
        channel: Integer (1 or 2), ``"CH1"``/``"CH2"``, ``"MATH"``,
            ``"REF1"``/``"REF2"``, or their lowercase variants.

    Returns:
        Normalized channel string (``"CH1"``, ``"CH2"``, ``"MATH"``,
        ``"REF1"``, or ``"REF2"``).

    Raises:
        ValueError: If the channel cannot be mapped to a valid identifier.
    """
    if isinstance(channel, int):
        if channel == 1:
            return "CH1"
        if channel == 2:
            return "CH2"
        raise ValueError(
            f"Invalid Tektronix channel {channel!r}; integer channels must be 1 or 2."
        )

    key = str(channel).strip().upper()
    if key in _CHANNEL_VALID_STRINGS:
        return key
    if key == "1":
        return "CH1"
    if key == "2":
        return "CH2"

    raise ValueError(
        f"Invalid Tektronix channel {channel!r}; "
        "expected 1/2, 'CH1'/'CH2', 'MATH', 'REF1', or 'REF2'."
    )


# ---------------------------------------------------------------------------
# Stub implementation for simulation mode
# ---------------------------------------------------------------------------


class _StubTektronixScope:
    """In-process stub that mimics ``UsbTmc`` without hardware.

    Generates synthetic sinusoidal waveforms so that experiment code can be
    exercised without a physical oscilloscope or a connected display.
    """

    def __init__(self) -> None:
        self._ch_config: dict[str, dict[str, Any]] = {
            "CH1": {"volts_per_div": 1.0, "coupling": "DC", "enabled": True},
            "CH2": {"volts_per_div": 1.0, "coupling": "DC", "enabled": True},
        }
        self._time_per_div: float = 1e-3
        self._trigger_level: float = 0.0
        self._trigger_source: str = "CH1"
        self._trigger_slope: str = "RISE"
        self._idn: str = "TEKTRONIX,TBS1052C,SIM000000,CF:91.1CT FV:v1.00"

    def command(self, cmd: str) -> None:
        """Parse and store SCPI configuration commands."""
        cmd = cmd.strip()
        for ch in ("CH1", "CH2"):
            if cmd.startswith(f"{ch}:SCALE "):
                self._ch_config[ch]["volts_per_div"] = float(cmd.split()[-1])
            elif cmd.startswith(f"{ch}:COUPLING "):
                self._ch_config[ch]["coupling"] = cmd.split()[-1]
            elif cmd.startswith(f"SELECT:{ch} "):
                self._ch_config[ch]["enabled"] = cmd.split()[-1].upper() in {"ON", "1"}
        if cmd.startswith("HORIZONTAL:SCALE "):
            self._time_per_div = float(cmd.split()[-1])
        elif cmd.startswith("TRIGGER:A:LEVEL "):
            self._trigger_level = float(cmd.split()[-1])
        elif cmd.startswith("TRIGGER:A:EDGE:SOURCE "):
            self._trigger_source = cmd.split()[-1]
        elif cmd.startswith("TRIGGER:A:EDGE:SLOPE "):
            self._trigger_slope = cmd.split()[-1]

    def query(self, cmd: str) -> str:
        """Return stored state for known SCPI queries."""
        cmd = cmd.strip()
        if cmd == "*IDN?":
            return self._idn
        if cmd == "ACQUIRE:STATE?":
            return "0"
        for ch in ("CH1", "CH2"):
            if cmd == f"{ch}:SCALE?":
                return str(self._ch_config[ch]["volts_per_div"])
            if cmd == f"{ch}:COUPLING?":
                return self._ch_config[ch]["coupling"]
        if cmd == "HORIZONTAL:SCALE?":
            return str(self._time_per_div)
        return ""

    def close(self) -> None:
        pass

    def read_waveform(
        self,
        channel: str,
        points: int,
    ) -> tuple[list[tuple[int, float, float, int]], str, str]:
        """Generate synthetic waveform rows in library format ``(index, time, V, raw)``.

        Args:
            channel: Tektronix channel string (``"CH1"``, ``"CH2"``, etc.).
            points: Number of waveform points to generate.

        Returns:
            Tuple of ``(rows, xunit, yunit)`` where each row is
            ``(index, time_s, voltage_v, raw_adc)``.
        """
        n = max(1, min(points, 2500))
        ch_cfg = self._ch_config.get(channel, {"volts_per_div": 1.0})
        volts_per_div: float = ch_cfg.get("volts_per_div", 1.0)
        time_per_div = self._time_per_div

        total_time = _SCREEN_DIVS * time_per_div
        times = [((i / max(n - 1, 1)) - 0.5) * total_time for i in range(n)]

        amp_divs = 3.0 if channel == "CH1" else 2.0
        amplitude_v = amp_divs * volts_per_div
        freq = 3.0 / total_time if total_time > 0 else 100.0
        phase = 0.0 if channel == "CH1" else -math.pi / 4

        rows: list[tuple[int, float, float, int]] = []
        for i, t in enumerate(times):
            voltage = amplitude_v * math.sin(2 * math.pi * freq * t + phase)
            raw = int(
                max(-128, min(127, round(voltage / max(volts_per_div, 1e-12) * 25)))
            )
            rows.append((i, t, voltage, raw))

        return rows, "s", "V"


# ---------------------------------------------------------------------------
# Hardware scope wrapper
# ---------------------------------------------------------------------------


class _TektronixHardwareScope:
    """Thin adapter unifying ``UsbTmc`` + the library ``read_waveform`` function.

    Both hardware and simulation paths use the same interface so
    ``TektronixTBS1000CBackend`` code is uniform regardless of mode.
    """

    def __init__(self, usbtmc: Any, read_waveform_fn: Any) -> None:
        self._usbtmc = usbtmc
        self._read_waveform = read_waveform_fn

    def command(self, cmd: str) -> None:
        self._usbtmc.command(cmd)

    def query(self, cmd: str) -> str:
        return self._usbtmc.query(cmd)

    def close(self) -> None:
        self._usbtmc.close()

    def read_waveform(
        self,
        channel: str,
        points: int,
    ) -> tuple[list[tuple], str, str]:
        return self._read_waveform(self._usbtmc, channel, points)


# ---------------------------------------------------------------------------
# Main backend adapter
# ---------------------------------------------------------------------------


@dataclass
class TektronixTBS1000CBackend(IOscilloscope):
    """OpenBench ``IOscilloscope`` adapter for the Tektronix TBS1000C-series.

    Wraps ``UsbTmc`` and ``read_waveform`` from the standalone
    ``tektronix-tbs1000c-linux`` project. The library communicates over the
    Linux USBTMC subsystem so no additional VISA installation is required.

    Simulation mode uses a ``_StubTektronixScope`` so experiments can run
    during development without a connected oscilloscope.

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: Path to the USBTMC device node (e.g. ``"/dev/usbtmc0"``).
            Defaults to ``"/dev/usbtmc0"`` when ``None``.
        simulate: When ``True``, a synthetic stub scope is used instead of
            opening hardware.
        acquire_points: Number of waveform samples to request per acquisition.
        acquire_timeout_s: Maximum seconds to wait for a single-sequence
            acquisition to complete.
    """

    acquire_points: int = 2500
    acquire_timeout_s: float = 5.0
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
        UsbTmc = _import_usbtmc()
        read_waveform_fn = _import_read_waveform()
        device = self.resource or "/dev/usbtmc0"
        usbtmc = UsbTmc(device)
        idn = usbtmc.query("*IDN?")
        logger.info("Tektronix IDN: %s", idn)
        self._scope = _TektronixHardwareScope(usbtmc, read_waveform_fn)

    def _disconnect(self) -> None:
        """Release the underlying USBTMC connection."""
        if self._scope is not None:
            self._scope.close()
        self._scope = None

    def connect(self) -> None:  # type: ignore[override]
        """Connect to hardware or initialize the simulation stub.

        Overrides the base ``connect`` so that simulation mode populates
        ``_scope`` with a stub, keeping all other methods uniform regardless
        of mode.
        """
        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._scope = _StubTektronixScope()
            self._status = InstrumentStatus.SIMULATED
            logger.info("Tektronix adapter using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("Tektronix connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("Tektronix adapter connected: %s", self.name)

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

        Sends ``SELECT:CHN``, ``CHN:COUPLING``, and ``CHN:SCALE`` SCPI
        commands to the scope.

        Args:
            channel: 1/2, ``"CH1"``/``"CH2"``, or their lowercase variants.
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
            ch,
            volts_per_div,
            coupling,
            enabled,
        )

        display_token = "ON" if enabled else "OFF"
        self._scope.command(f"SELECT:{ch} {display_token}")
        self._scope.command(f"{ch}:COUPLING {coupling.upper()}")
        self._scope.command(f"{ch}:SCALE {volts_per_div!r}")

    def configure_timebase(
        self,
        time_per_div_s: float,
        *,
        trigger_level_v: float = 0.0,
        trigger_channel: InstrumentChannel = 1,
        trigger_slope: str = "rising",
    ) -> None:
        """Configure horizontal timebase and edge trigger settings.

        Sends ``HORIZONTAL:SCALE``, ``TRIGGER:A:TYPE``,
        ``TRIGGER:A:EDGE:SOURCE``, ``TRIGGER:A:EDGE:SLOPE``, and
        ``TRIGGER:A:LEVEL`` SCPI commands to the scope.

        Args:
            time_per_div_s: Horizontal scale in seconds per division.
            trigger_level_v: Trigger threshold voltage in volts.
            trigger_channel: Channel used as the trigger source.
            trigger_slope: ``"rising"`` or ``"falling"`` edge trigger.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the trigger channel is not valid.
        """
        self._require_scope()
        trig_ch = _normalize_channel(trigger_channel)
        slope_scpi = "RISE" if trigger_slope.lower() == "rising" else "FALL"

        self._timebase = {
            "time_per_div_s": time_per_div_s,
            "trigger_level_v": trigger_level_v,
            "trigger_channel": trigger_channel,
            "trigger_slope": trigger_slope,
        }
        logger.debug(
            "configure_timebase: %.3g s/div, trigger %.3g V (%s %s)",
            time_per_div_s,
            trigger_level_v,
            trig_ch,
            slope_scpi,
        )

        self._scope.command(f"HORIZONTAL:SCALE {time_per_div_s!r}")
        self._scope.command("TRIGGER:A:TYPE EDGE")
        self._scope.command(f"TRIGGER:A:EDGE:SOURCE {trig_ch}")
        self._scope.command(f"TRIGGER:A:EDGE:SLOPE {slope_scpi}")
        self._scope.command(f"TRIGGER:A:LEVEL {trigger_level_v!r}")

    def acquire(self, channel: InstrumentChannel) -> OscilloscopeReading:
        """Acquire a single-shot waveform from the specified channel.

        Arms the scope for a single-sequence acquisition, waits for
        completion, then transfers and converts the waveform. Simulation
        mode returns a synthetic waveform without hardware access.

        Args:
            channel: 1/2, ``"CH1"``/``"CH2"``, ``"MATH"``, ``"REF1"``,
                or ``"REF2"``.

        Returns:
            Acquired or simulated time-domain waveform.

        Raises:
            RuntimeError: If the adapter is not connected or no data arrives.
            TimeoutError: If the acquisition does not complete within
                ``acquire_timeout_s``.
            ValueError: If the channel identifier is not valid.
        """
        self._require_scope()
        ch = _normalize_channel(channel)

        if not self.simulate:
            self._scope.command("ACQUIRE:STOPAFTER SEQUENCE")
            self._scope.command("ACQUIRE:STATE RUN")
            self._wait_for_trigger()

        rows, xunit, yunit = self._scope.read_waveform(ch, self.acquire_points)

        if not rows:
            raise RuntimeError(
                f"No waveform data returned for channel {channel!r}."
            )

        times = [row[1] for row in rows]
        volts = [row[2] for row in rows]
        n = len(times)
        time_span = times[-1] - times[0] if n > 1 else 1.0
        sample_rate = float(n - 1) / time_span if time_span > 0 else 1.0

        return OscilloscopeReading(
            channel=channel,
            time_s=times,
            voltage_v=volts,
            sample_rate_hz=sample_rate,
            metadata={
                "tektronix_channel": ch,
                "points": n,
                "xunit": xunit,
                "yunit": yunit,
                "backend": (
                    "tektronix_tbs1000c" if not self.simulate else "tektronix_sim"
                ),
            },
        )

    # ------------------------------------------------------------------
    # Extra Tektronix-specific helpers
    # ------------------------------------------------------------------

    def identify(self) -> str:
        """Query and return the scope's ``*IDN?`` response.

        Returns:
            IDN string from the oscilloscope.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        return self._scope.query("*IDN?")

    def autoscale(self) -> None:
        """Send ``AUTOSET EXECUTE`` to let the scope configure its own scales.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        logger.debug("Tektronix autoscale triggered")
        self._scope.command("AUTOSET EXECUTE")

    def run(self) -> None:
        """Put the scope into continuous acquisition mode.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        self._scope.command("ACQUIRE:STATE RUN")

    def stop(self) -> None:
        """Halt ongoing acquisitions on the scope.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        self._scope.command("ACQUIRE:STATE STOP")

    def force_trigger(self) -> None:
        """Force a trigger event using ``TRIGGER FORCE``.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        self._scope.command("TRIGGER FORCE")

    def send_command(self, command: str) -> None:
        """Send an arbitrary SCPI command to the scope.

        Args:
            command: Raw SCPI command string.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        logger.debug("Tektronix raw command: %s", command)
        self._scope.command(command)

    def query(self, command: str) -> str:
        """Send an arbitrary SCPI query and return the response string.

        Args:
            command: Raw SCPI query string ending with ``?``.

        Returns:
            Decoded response string with surrounding whitespace stripped.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_scope()
        logger.debug("Tektronix raw query: %s", command)
        return self._scope.query(command)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_scope(self) -> None:
        if self._scope is None:
            raise RuntimeError(
                f"Tektronix adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )

    def _wait_for_trigger(self) -> None:
        """Poll ``ACQUIRE:STATE?`` until acquisition stops or the timeout elapses.

        Raises:
            TimeoutError: If the acquisition has not completed within
                ``acquire_timeout_s``.
        """
        deadline = time.monotonic() + self.acquire_timeout_s
        while time.monotonic() < deadline:
            try:
                state = self._scope.query("ACQUIRE:STATE?").strip().upper()
            except Exception:
                logger.debug("Acquire state query failed — retrying", exc_info=True)
                time.sleep(0.05)
                continue

            # "0" = stopped (acquisition complete), "1" = running
            if state not in {"1", "RUN", "ON"}:
                return
            time.sleep(0.05)

        raise TimeoutError(
            f"Tektronix acquisition did not complete within "
            f"{self.acquire_timeout_s:.1f} s."
        )


__all__ = ["TektronixTBS1000CBackend"]
