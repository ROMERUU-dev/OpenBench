"""VirtualBench backend adapters for OpenBench.

Wraps the standalone ``vbarrido-py`` project (~/virtualBench-NI) without
reimplementing instrument communication. Three adapters are provided:

- VirtualBenchOscilloscopeBackend  → IOscilloscope  (MSO 2 analog channels)
- VirtualBenchFGenBackend          → IFunctionGenerator (FGEN single channel)
- VirtualBenchPSBackend            → IDCSupply (PS rails: +25V, −25V, +6V)

Hardware note: NI VirtualBench supports a single active connection per device.
When pairing OSC and FGEN adapters on the same unit, ensure only one is open
at a time, or manage the shared ``PyVirtualBench`` session externally.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from openbench.core.interfaces import (
    DCSweepReading,
    FrequencySweepPoint,
    IDCSupply,
    IFunctionGenerator,
    IOscilloscope,
    InstrumentChannel,
    InstrumentStatus,
    OscilloscopeReading,
    WaveformConfig,
)

logger = logging.getLogger(__name__)

_LIB_ROOT = Path.home() / "virtualBench-NI"
_DEFAULT_SAMPLE_RATE_HZ = 1_000_000.0
_DEFAULT_NUM_DIVS = 10

_MSO_CHANNEL_MAP: dict = {
    1: "mso/1",
    "1": "mso/1",
    "mso/1": "mso/1",
    "CH1": "mso/1",
    2: "mso/2",
    "2": "mso/2",
    "mso/2": "mso/2",
    "CH2": "mso/2",
}
_PS_CHANNEL_MAP: dict = {
    1: "ps/+25V",
    "+25V": "ps/+25V",
    "ps/+25V": "ps/+25V",
    "POS": "ps/+25V",
    2: "ps/-25V",
    "-25V": "ps/-25V",
    "ps/-25V": "ps/-25V",
    "NEG": "ps/-25V",
    3: "ps/+6V",
    "+6V": "ps/+6V",
    "ps/+6V": "ps/+6V",
    "6V": "ps/+6V",
}


# ---------------------------------------------------------------------------
# Path / import helpers
# ---------------------------------------------------------------------------


def _ensure_lib_on_path() -> bool:
    """Insert the vbarrido-py source root into ``sys.path`` when present.

    Returns:
        ``True`` when the source directory exists and is on ``sys.path``.
    """
    lib = str(_LIB_ROOT / "src")
    if lib in sys.path:
        return True
    if (_LIB_ROOT / "src").is_dir():
        sys.path.insert(0, lib)
        logger.debug("Added vbarrido-py to sys.path: %s", lib)
        return True
    logger.warning("vbarrido-py not found at %s", _LIB_ROOT)
    return False


def _import_vbarrido_instrument() -> tuple[Any, Any]:
    """Import backend classes from vbarrido-py.

    Returns:
        Tuple of ``(VirtualBenchPyBackend, SimulatedBackend)`` classes.

    Raises:
        ImportError: When the library directory is absent or import fails.
    """
    if not _ensure_lib_on_path():
        raise ImportError(
            f"vbarrido-py not found at {_LIB_ROOT}. "
            "Clone the project there or use simulate=True."
        )
    from vbarrido_py.instrument import SimulatedBackend, VirtualBenchPyBackend  # type: ignore[import]

    return VirtualBenchPyBackend, SimulatedBackend


def _import_pyvirtualbench() -> Any:
    """Import ``PyVirtualBench`` from pyvirtualbench.

    Returns:
        The ``PyVirtualBench`` class.

    Raises:
        ImportError: When pyvirtualbench is not installed.
    """
    try:
        from pyvirtualbench import PyVirtualBench  # type: ignore[import]

        return PyVirtualBench
    except ImportError as exc:
        raise ImportError(
            "pyvirtualbench not installed. Install the NI VirtualBench Python "
            "driver or use simulate=True."
        ) from exc


# ---------------------------------------------------------------------------
# Channel normalization
# ---------------------------------------------------------------------------


def _normalize_mso_channel(channel: InstrumentChannel) -> str:
    """Map an OpenBench channel identifier to a VirtualBench MSO channel string.

    Args:
        channel: Integer (1 or 2), ``"CH1"``, ``"CH2"``, ``"mso/1"``, or ``"mso/2"``.

    Returns:
        Normalized string such as ``"mso/1"``.

    Raises:
        ValueError: If the channel cannot be mapped.
    """
    key: Any = channel if isinstance(channel, int) else str(channel).strip()
    result = _MSO_CHANNEL_MAP.get(key)
    if result is None and isinstance(key, str):
        for k, v in _MSO_CHANNEL_MAP.items():
            if isinstance(k, str) and k.upper() == key.upper():
                return v
    if result is None:
        raise ValueError(
            f"Invalid VirtualBench MSO channel {channel!r}; "
            "expected 1/2, 'CH1'/'CH2', or 'mso/1'/'mso/2'."
        )
    return result


def _normalize_ps_channel(channel: InstrumentChannel) -> str:
    """Map an OpenBench channel identifier to a VirtualBench PS rail string.

    Args:
        channel: Integer (1–3), ``"+25V"``/``"-25V"``/``"+6V"``,
            ``"POS"``/``"NEG"``, or fully qualified ``"ps/+25V"`` etc.

    Returns:
        Normalized string such as ``"ps/+25V"``.

    Raises:
        ValueError: If the channel cannot be mapped.
    """
    key: Any = channel if isinstance(channel, int) else str(channel).strip()
    result = _PS_CHANNEL_MAP.get(key)
    if result is None and isinstance(key, str):
        for k, v in _PS_CHANNEL_MAP.items():
            if isinstance(k, str) and k.upper() == key.upper():
                return v
    if result is None:
        raise ValueError(
            f"Invalid VirtualBench PS channel {channel!r}; "
            "expected 1–3, '+25V'/'-25V'/'+6V', 'POS'/'NEG', or 'ps/+…' strings."
        )
    return result


# ---------------------------------------------------------------------------
# pyvirtualbench helper utilities
# ---------------------------------------------------------------------------


def _ps_configure_voltage(
    ps: Any, channel_str: str, voltage_v: float, current_limit_a: float
) -> None:
    """Apply voltage and current-limit settings to a PS rail.

    Handles both ctypes-wchar_p and plain-string API variants of pyvirtualbench.

    Args:
        ps: Acquired PS module from ``PyVirtualBench``.
        channel_str: Fully qualified rail string (``"ps/+25V"`` etc.).
        voltage_v: Voltage setpoint in volts.
        current_limit_a: Current compliance limit in amperes.
    """
    try:
        from ctypes import c_wchar_p

        ps.configure_voltage_output(c_wchar_p(channel_str), voltage_v, current_limit_a)
    except TypeError:
        ps.configure_voltage_output(channel_str, voltage_v, current_limit_a)


def _ps_read_output(ps: Any, channel_str: str) -> tuple[float, float]:
    """Read actual voltage and current from a PS rail.

    Args:
        ps: Acquired PS module from ``PyVirtualBench``.
        channel_str: Fully qualified rail string.

    Returns:
        Tuple of ``(measured_voltage_v, measured_current_a)``.
    """
    try:
        from ctypes import c_wchar_p

        v, i, _ = ps.read_output(c_wchar_p(channel_str))
    except TypeError:
        v, i, _ = ps.read_output(channel_str)
    return float(v), float(i)


# ---------------------------------------------------------------------------
# Stub classes (used in simulate mode when pyvirtualbench is absent)
# ---------------------------------------------------------------------------


class _StubMSO:
    """Minimal MSO stub for simulation without pyvirtualbench."""

    def __init__(self) -> None:
        self._sample_rate_hz: float = _DEFAULT_SAMPLE_RATE_HZ
        self._sample_count: int = 1024
        self._ch_range: dict[str, float] = {"mso/1": 5.0, "mso/2": 5.0}

    def configure_analog_channel(
        self,
        channel: str,
        enabled: bool,
        range_v: float,
        offset_v: float,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._ch_range[channel] = range_v

    def configure_timing(
        self,
        sample_rate_hz: float,
        duration_s: float,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._sample_rate_hz = sample_rate_hz
        self._sample_count = max(64, int(sample_rate_hz * duration_s))

    def configure_sample_clock_timing(self, sample_rate: float, sample_count: int) -> None:
        self._sample_rate_hz = sample_rate
        self._sample_count = sample_count

    def configure_analog_edge_trigger(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self, *args: Any, **kwargs: Any) -> None:
        pass

    def stop(self) -> None:
        pass

    def read_analog(self, n: int) -> tuple[list[float], list[float]]:
        sr = self._sample_rate_hz
        t = np.arange(self._sample_count) / sr
        amp1 = self._ch_range.get("mso/1", 5.0) * 0.4
        amp2 = self._ch_range.get("mso/2", 5.0) * 0.3
        freq = max(sr / self._sample_count * 5, 1000.0)
        ch1 = (amp1 * np.sin(2 * np.pi * freq * t)).tolist()
        ch2 = (amp2 * np.sin(2 * np.pi * freq * t - np.pi / 4)).tolist()
        return ch1, ch2

    def release(self) -> None:
        pass


class _StubFGen:
    """Minimal FGEN stub for simulation without pyvirtualbench."""

    def configure_standard_waveform(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def release(self) -> None:
        pass


def _ctypes_to_str(channel: Any) -> str:
    """Extract the string value from a channel argument that may be a ctypes object.

    pyvirtualbench passes channel names as ``ctypes.c_wchar_p`` objects. Stubs
    and helpers that accept ``Any`` need to unwrap them to plain strings.
    """
    val = getattr(channel, "value", channel)
    if val is None:
        return ""
    return str(val).strip().strip("\x00")


class _StubPS:
    """Minimal PS stub for simulation without pyvirtualbench."""

    def __init__(self) -> None:
        self._rails: dict[str, dict[str, float]] = {
            "ps/+25V": {"voltage": 0.0, "current_limit": 0.1, "enabled": 0.0},
            "ps/-25V": {"voltage": 0.0, "current_limit": 0.1, "enabled": 0.0},
            "ps/+6V": {"voltage": 0.0, "current_limit": 0.1, "enabled": 0.0},
        }

    def configure_voltage_output(
        self, channel: Any, voltage_v: float, current_limit_a: float
    ) -> None:
        ch = _ctypes_to_str(channel)
        if ch in self._rails:
            self._rails[ch]["voltage"] = voltage_v
            self._rails[ch]["current_limit"] = current_limit_a

    def enable_all_outputs(self, enabled: bool) -> None:
        flag = 1.0 if enabled else 0.0
        for rail in self._rails.values():
            rail["enabled"] = flag

    def read_output(self, channel: Any) -> tuple[float, float, None]:
        ch = _ctypes_to_str(channel)
        rail = self._rails.get(ch, {"voltage": 0.0, "current_limit": 0.0, "enabled": 0.0})
        if not rail["enabled"]:
            return 0.0, 0.0, None
        v = rail["voltage"]
        i = min(abs(v) / 1_000.0, rail["current_limit"]) * (1.0 if v >= 0 else -1.0)
        return v, i, None

    def release(self) -> None:
        pass


# ---------------------------------------------------------------------------
# VirtualBenchOscilloscopeBackend
# ---------------------------------------------------------------------------


@dataclass
class VirtualBenchOscilloscopeBackend(IOscilloscope):
    """OpenBench ``IOscilloscope`` adapter for the NI VirtualBench MSO.

    Wraps the vbarrido-py library and pyvirtualbench for hardware access.
    Simulation mode uses numpy-generated synthetic waveforms so experiments
    can run without hardware.

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: VirtualBench device name (e.g. ``"VB8012-30DF172"``). When
            ``None`` and ``simulate`` is ``False``, auto-discovery is attempted.
        simulate: When ``True``, synthetic waveforms are returned.
        sim_frequency_hz: Frequency of the synthetic waveform in simulation mode.
        sim_amplitude_v: Peak amplitude of the synthetic waveform in volts.
    """

    sim_frequency_hz: float = 1_000.0
    sim_amplitude_v: float = 1.0

    _vb: Any = field(default=None, init=False, repr=False)
    _mso: Any = field(default=None, init=False, repr=False)
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
        """Open PyVirtualBench and acquire the MSO module."""
        PyVirtualBench = _import_pyvirtualbench()
        resource = self.resource or ""
        if not resource:
            try:
                _, _ = _import_vbarrido_instrument()
                from vbarrido_py.instrument import discover_virtualbench_resource  # type: ignore[import]

                resource = discover_virtualbench_resource()
                logger.info("VirtualBench auto-discovered: %s", resource)
            except Exception as exc:
                raise RuntimeError(
                    "VirtualBench resource not specified and auto-discovery failed."
                ) from exc

        self._vb = PyVirtualBench(resource)
        for name in ("acquire_mixed_signal_oscilloscope", "get_mixed_signal_oscilloscope", "mso"):
            candidate = getattr(self._vb, name, None)
            if candidate is not None:
                self._mso = candidate() if callable(candidate) else candidate
                break
        if self._mso is None:
            raise RuntimeError("Could not acquire MSO module from PyVirtualBench.")
        logger.info("VirtualBench MSO acquired: %s", resource)

    def _disconnect(self) -> None:
        """Release the MSO module and close the VirtualBench session."""
        for obj in (self._mso, self._vb):
            if obj is None:
                continue
            for close_name in ("release", "close"):
                fn = getattr(obj, close_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        logger.debug("Error closing VB OSC object", exc_info=True)
                    break
        self._mso = None
        self._vb = None

    # Simulation mode: create stub MSO so the other methods work uniformly
    def connect(self) -> None:  # type: ignore[override]
        """Connect to hardware or initialize simulation stub.

        Overrides the base ``connect`` so that simulation mode populates
        ``_mso`` with a stub, keeping method implementations uniform.
        """
        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._mso = _StubMSO()
            self._status = InstrumentStatus.SIMULATED
            logger.info("VirtualBench OSC using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("VirtualBench OSC connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("VirtualBench OSC connected: %s", self.name)

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

        Args:
            channel: 1/2, ``"CH1"``/``"CH2"``, or ``"mso/1"``/``"mso/2"``.
            volts_per_div: Vertical scale in volts per division.
            coupling: Input coupling — ``"DC"``, ``"AC"``, or ``"GND"``.
            enabled: ``True`` to display the channel.

        Raises:
            ValueError: If the channel identifier is not valid.
            RuntimeError: If the adapter is not connected.
        """
        self._require_mso()
        ch_str = _normalize_mso_channel(channel)
        self._ch_configs[ch_str] = {
            "volts_per_div": volts_per_div,
            "coupling": coupling,
            "enabled": enabled,
        }
        logger.debug("configure_channel %s: %.3g V/div, %s, enabled=%s", ch_str, volts_per_div, coupling, enabled)

        if not self.simulate:
            self._apply_mso_channel(ch_str)

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
            trigger_level_v: Trigger threshold in volts.
            trigger_channel: Channel used as the trigger source.
            trigger_slope: ``"rising"`` or ``"falling"`` edge.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_mso()
        self._timebase = {
            "time_per_div_s": time_per_div_s,
            "trigger_level_v": trigger_level_v,
            "trigger_channel": trigger_channel,
            "trigger_slope": trigger_slope,
        }
        logger.debug("configure_timebase: %.3g s/div, trigger %.3g V", time_per_div_s, trigger_level_v)

        if not self.simulate:
            self._apply_mso_timing()

    def acquire(self, channel: InstrumentChannel) -> OscilloscopeReading:
        """Acquire a waveform from the specified channel.

        In simulation mode, returns a synthetic sine wave based on the
        current timebase and channel configuration.

        Args:
            channel: Channel identifier (1/2, ``"CH1"``/``"CH2"``).

        Returns:
            Acquired or simulated time-domain waveform.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_mso()
        ch_str = _normalize_mso_channel(channel)

        if self.simulate:
            return self._simulate_acquire(channel, ch_str)

        self._apply_mso_channel(ch_str)
        self._apply_mso_timing()

        try:
            self._mso.run(True)
        except TypeError:
            self._mso.run()

        sample_rate, num_samples = self._compute_timing()
        data = self._mso.read_analog(num_samples * 2)

        if not (isinstance(data, (list, tuple)) and len(data) >= 2):
            raise RuntimeError("MSO read_analog returned an unexpected data format.")

        ch_index = 0 if ch_str == "mso/1" else 1
        voltage_data = [float(v) for v in list(data[ch_index])[:num_samples]]
        time_data = [i / sample_rate for i in range(len(voltage_data))]

        return OscilloscopeReading(
            channel=channel,
            time_s=time_data,
            voltage_v=voltage_data,
            sample_rate_hz=sample_rate,
            metadata={"mso_channel": ch_str, "backend": "virtualbench"},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_mso(self) -> None:
        if self._mso is None:
            raise RuntimeError(
                f"VirtualBench OSC adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )

    def _compute_timing(self) -> tuple[float, int]:
        """Return ``(sample_rate_hz, sample_count)`` from stored timebase config."""
        time_per_div = self._timebase.get("time_per_div_s", 1e-3)
        duration_s = _DEFAULT_NUM_DIVS * time_per_div
        # Aim for at least 512 samples; clamp between 20 kHz and 20 MHz
        sample_rate = float(np.clip(_DEFAULT_SAMPLE_RATE_HZ, 20_000.0, 20_000_000.0))
        num_samples = max(512, int(sample_rate * duration_s))
        return sample_rate, num_samples

    def _apply_mso_channel(self, ch_str: str) -> None:
        cfg = self._ch_configs.get(ch_str, {"volts_per_div": 1.0, "coupling": "DC", "enabled": True})
        range_v = cfg["volts_per_div"] * _DEFAULT_NUM_DIVS
        coupling = cfg.get("coupling", "DC")
        enabled = cfg.get("enabled", True)

        configure = getattr(self._mso, "configure_analog_channel", None)
        if callable(configure):
            try:
                from pyvirtualbench.pyvirtualbench import MsoCoupling, MsoProbeAttenuation  # type: ignore[import]

                coupling_enum = getattr(MsoCoupling, coupling.upper(), MsoCoupling.DC)
                probe_enum = getattr(MsoProbeAttenuation, "ATTENUATION_1X", None)
                configure(ch_str, enabled, range_v, 0.0, probe_enum, coupling_enum)
            except (ImportError, TypeError):
                try:
                    configure(ch_str, enabled, range_v, 0.0)
                except Exception:
                    logger.debug("configure_analog_channel failed for %s", ch_str, exc_info=True)

    def _apply_mso_timing(self) -> None:
        sample_rate, num_samples = self._compute_timing()
        duration_s = num_samples / sample_rate
        trig_level = self._timebase.get("trigger_level_v", 0.0)
        trig_ch_id = self._timebase.get("trigger_channel", 1)
        trig_slope = self._timebase.get("trigger_slope", "rising")
        trig_ch_str = _normalize_mso_channel(trig_ch_id)

        for name in ("configure_timing", "configure_sample_clock_timing"):
            method = getattr(self._mso, name, None)
            if not callable(method):
                continue
            try:
                from pyvirtualbench.pyvirtualbench import MsoSamplingMode  # type: ignore[import]

                method(sample_rate, duration_s, 1e-6, MsoSamplingMode.SAMPLE)
            except (ImportError, TypeError):
                try:
                    method(sample_rate, sample_count=num_samples)
                except TypeError:
                    method(sample_rate, num_samples)
            break

        trig_fn = getattr(self._mso, "configure_analog_edge_trigger", None)
        if callable(trig_fn):
            try:
                from pyvirtualbench.pyvirtualbench import Edge, MsoTriggerInstance  # type: ignore[import]

                slope_enum = Edge.RISING if trig_slope.lower() == "rising" else Edge.FALLING
                trig_fn(trig_ch_str, slope_enum, trig_level, 0.01, MsoTriggerInstance.A)
            except (ImportError, TypeError):
                try:
                    trig_fn(trig_ch_str, level=trig_level, slope=trig_slope.lower())
                except Exception:
                    logger.debug("configure_analog_edge_trigger failed", exc_info=True)

    def _simulate_acquire(
        self, channel: InstrumentChannel, ch_str: str
    ) -> OscilloscopeReading:
        """Generate a synthetic waveform for simulation mode."""
        time_per_div = self._timebase.get("time_per_div_s", 1e-3)
        duration_s = _DEFAULT_NUM_DIVS * time_per_div
        sample_rate = float(np.clip(
            max(_DEFAULT_SAMPLE_RATE_HZ, 100.0 * self.sim_frequency_hz),
            20_000.0,
            20_000_000.0,
        ))
        num_samples = max(512, int(sample_rate * duration_s))
        t = np.arange(num_samples) / sample_rate
        phase_offset = 0.0 if ch_str == "mso/1" else -np.pi / 4
        noise = np.random.default_rng(0).normal(0.0, self.sim_amplitude_v * 0.005, num_samples)
        voltage = self.sim_amplitude_v * np.sin(
            2 * np.pi * self.sim_frequency_hz * t + phase_offset
        ) + noise

        return OscilloscopeReading(
            channel=channel,
            time_s=t.tolist(),
            voltage_v=voltage.tolist(),
            sample_rate_hz=sample_rate,
            metadata={"mso_channel": ch_str, "backend": "virtualbench_sim"},
        )


# ---------------------------------------------------------------------------
# VirtualBenchFGenBackend
# ---------------------------------------------------------------------------


@dataclass
class VirtualBenchFGenBackend(IFunctionGenerator):
    """OpenBench ``IFunctionGenerator`` adapter for the NI VirtualBench FGEN.

    Wraps pyvirtualbench for hardware access. Simulation mode tracks waveform
    state in-process so experiments can run without hardware.

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: VirtualBench device name. ``None`` triggers auto-discovery.
        simulate: When ``True``, no hardware access occurs.
    """

    _vb: Any = field(default=None, init=False, repr=False)
    _fgen: Any = field(default=None, init=False, repr=False)
    _current_config: WaveformConfig | None = field(default=None, init=False, repr=False)
    _output_enabled: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open PyVirtualBench and acquire the FGEN module."""
        PyVirtualBench = _import_pyvirtualbench()
        resource = self.resource or ""
        self._vb = PyVirtualBench(resource)
        for name in ("acquire_function_generator", "get_function_generator", "fgen"):
            candidate = getattr(self._vb, name, None)
            if candidate is not None:
                self._fgen = candidate() if callable(candidate) else candidate
                break
        if self._fgen is None:
            raise RuntimeError("Could not acquire FGEN module from PyVirtualBench.")
        logger.info("VirtualBench FGEN acquired: %s", resource or "auto")

    def _disconnect(self) -> None:
        """Release the FGEN module and close the VirtualBench session."""
        for obj in (self._fgen, self._vb):
            if obj is None:
                continue
            for close_name in ("release", "close"):
                fn = getattr(obj, close_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        logger.debug("Error closing VB FGEN object", exc_info=True)
                    break
        self._fgen = None
        self._vb = None

    def connect(self) -> None:  # type: ignore[override]
        """Connect to hardware or initialize simulation stub."""
        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._fgen = _StubFGen()
            self._status = InstrumentStatus.SIMULATED
            logger.info("VirtualBench FGEN using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("VirtualBench FGEN connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("VirtualBench FGEN connected: %s", self.name)

    # ------------------------------------------------------------------
    # IFunctionGenerator interface
    # ------------------------------------------------------------------

    def configure(self, config: WaveformConfig) -> None:
        """Apply a waveform configuration to the FGEN output.

        Args:
            config: Complete waveform specification to apply.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the waveform type is not supported by the hardware.
        """
        self._require_fgen()
        self._current_config = config
        logger.debug(
            "configure FGEN: %s %.6g Hz %.6g Vpp",
            config.waveform, config.frequency_hz, config.amplitude_v,
        )
        self._apply_fgen_config(config)

    def enable_output(
        self, channel: InstrumentChannel = 1, *, enabled: bool = True
    ) -> None:
        """Enable or disable the FGEN signal output.

        Args:
            channel: Channel identifier (VirtualBench FGEN has one channel).
            enabled: ``True`` to start, ``False`` to stop the output.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_fgen()
        self._output_enabled = enabled
        logger.debug("FGEN output %s", "enabled" if enabled else "disabled")

        if enabled:
            run = getattr(self._fgen, "run", None) or getattr(self._fgen, "start", None)
            if callable(run):
                run()
        else:
            stop = getattr(self._fgen, "stop", None)
            if callable(stop):
                stop()

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
        """Sweep the output frequency and return the applied setpoints.

        The FGEN output is enabled before the sweep; it remains at ``stop_hz``
        after completion. No measurements are captured — pair with
        ``VirtualBenchOscilloscopeBackend.acquire`` to read responses.

        Args:
            channel: Channel identifier (VirtualBench has one FGEN channel).
            start_hz: Starting frequency in hertz.
            stop_hz: Ending frequency in hertz.
            num_points: Number of frequency steps including endpoints.
            amplitude_v: Peak-to-peak amplitude in volts.
            log_scale: ``True`` for logarithmic spacing, ``False`` for linear.
            dwell_s: Settling time in seconds at each frequency step.
            waveform: Waveform shape (``"sine"``, ``"square"``, ``"triangle"``).

        Returns:
            Ordered list of ``FrequencySweepPoint`` setpoints applied.

        Raises:
            ValueError: If ``num_points`` is less than 2.
            RuntimeError: If the adapter is not connected.
        """
        self._require_fgen()

        if num_points < 2:
            raise ValueError("num_points must be >= 2.")

        frequencies = (
            np.geomspace(start_hz, stop_hz, num_points)
            if log_scale
            else np.linspace(start_hz, stop_hz, num_points)
        )

        points: list[FrequencySweepPoint] = []
        for freq in frequencies:
            cfg = WaveformConfig(
                waveform=waveform,
                frequency_hz=float(freq),
                amplitude_v=amplitude_v,
                channel=channel,
            )
            self._apply_fgen_config(cfg)
            self.enable_output(channel, enabled=True)

            if dwell_s > 0:
                time.sleep(dwell_s)

            points.append(FrequencySweepPoint(frequency_hz=float(freq), channel=channel))
            logger.debug("FGEN sweep step: %.6g Hz", freq)

        return points

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_fgen(self) -> None:
        if self._fgen is None:
            raise RuntimeError(
                f"VirtualBench FGEN adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )

    def _apply_fgen_config(self, config: WaveformConfig) -> None:
        """Apply a WaveformConfig to the underlying FGEN module."""
        for name in ("configure_standard_waveform", "configure_standard_waveform_fgen"):
            method = getattr(self._fgen, name, None)
            if not callable(method):
                continue
            try:
                from pyvirtualbench.pyvirtualbench import Waveform  # type: ignore[import]

                wf_enum = getattr(Waveform, config.waveform.upper(), Waveform.SINE)
                method(wf_enum, config.amplitude_v, config.offset_v, config.frequency_hz, 50.0)
            except (ImportError, TypeError):
                try:
                    method(
                        waveform=config.waveform.lower(),
                        amplitude=config.amplitude_v,
                        dc_offset=config.offset_v,
                        frequency=config.frequency_hz,
                    )
                except Exception:
                    logger.debug("configure_standard_waveform failed", exc_info=True)
            return
        logger.warning("FGEN module has no compatible configure_standard_waveform method.")


# ---------------------------------------------------------------------------
# VirtualBenchPSBackend
# ---------------------------------------------------------------------------


@dataclass
class VirtualBenchPSBackend(IDCSupply):
    """OpenBench ``IDCSupply`` adapter for the NI VirtualBench power supply.

    The VirtualBench PS exposes three rails: ``ps/+25V``, ``ps/−25V``,
    ``ps/+6V``. Channels may be addressed by integer (1–3), sign-only strings
    (``"+25V"``), aliases (``"POS"``/``"NEG"``), or fully qualified strings.

    Note: ``enable_all_outputs`` on the VirtualBench PS acts on ALL rails
    simultaneously. Setting one rail's voltage enables all rails together.

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: VirtualBench device name. ``None`` triggers auto-discovery.
        simulate: When ``True``, no hardware access occurs.
    """

    _vb: Any = field(default=None, init=False, repr=False)
    _ps: Any = field(default=None, init=False, repr=False)
    _ch_state: dict[str, dict[str, float]] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._ch_state = {
            "ps/+25V": {"voltage": 0.0, "current_limit": 0.1},
            "ps/-25V": {"voltage": 0.0, "current_limit": 0.1},
            "ps/+6V": {"voltage": 0.0, "current_limit": 0.1},
        }

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open PyVirtualBench and acquire the PS module."""
        PyVirtualBench = _import_pyvirtualbench()
        resource = self.resource or ""
        self._vb = PyVirtualBench(resource)
        acquire_ps = getattr(self._vb, "acquire_power_supply", None)
        if not callable(acquire_ps):
            raise RuntimeError("PyVirtualBench object has no acquire_power_supply method.")
        self._ps = acquire_ps()
        logger.info("VirtualBench PS acquired: %s", resource or "auto")

    def _disconnect(self) -> None:
        """Release the PS module and close the VirtualBench session."""
        for obj in (self._ps, self._vb):
            if obj is None:
                continue
            for close_name in ("release", "close"):
                fn = getattr(obj, close_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        logger.debug("Error closing VB PS object", exc_info=True)
                    break
        self._ps = None
        self._vb = None

    def connect(self) -> None:  # type: ignore[override]
        """Connect to hardware or initialize simulation stub."""
        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._ps = _StubPS()
            self._status = InstrumentStatus.SIMULATED
            logger.info("VirtualBench PS using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("VirtualBench PS connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("VirtualBench PS connected: %s", self.name)

    # ------------------------------------------------------------------
    # IDCSupply interface
    # ------------------------------------------------------------------

    def set_voltage(self, channel: InstrumentChannel, voltage_v: float) -> None:
        """Set the output voltage for a VirtualBench PS rail.

        Args:
            channel: Rail identifier — 1–3, ``"+25V"``, ``"-25V"``, ``"+6V"``,
                ``"POS"``, ``"NEG"``, or fully qualified ``"ps/+25V"`` etc.
            voltage_v: Voltage setpoint in volts.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the channel identifier is not valid.
        """
        self._require_ps()
        ch = _normalize_ps_channel(channel)
        self._ch_state[ch]["voltage"] = voltage_v
        logger.debug("VB PS set_voltage %s → %.6g V", ch, voltage_v)
        _ps_configure_voltage(
            self._ps, ch, voltage_v, self._ch_state[ch]["current_limit"]
        )

    def set_current(self, channel: InstrumentChannel, current_a: float) -> None:
        """Set the current compliance limit for a VirtualBench PS rail.

        Args:
            channel: Rail identifier.
            current_a: Current compliance limit in amperes.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the channel identifier is not valid.
        """
        self._require_ps()
        ch = _normalize_ps_channel(channel)
        self._ch_state[ch]["current_limit"] = current_a
        logger.debug("VB PS set_current %s → %.6g A", ch, current_a)
        _ps_configure_voltage(
            self._ps, ch, self._ch_state[ch]["voltage"], current_a
        )

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
        """Sweep a VirtualBench PS rail across voltage setpoints.

        The rail outputs are enabled before the sweep and remain at ``stop_v``
        after completion. The VirtualBench PS enables **all** rails together
        when outputs are turned on.

        Args:
            channel: Rail identifier.
            start_v: First voltage setpoint in volts.
            stop_v: Final voltage boundary in volts.
            step_v: Voltage step magnitude; sign is ignored.
            current_limit_a: Optional current compliance limit in amperes.
            dwell_s: Settling time in seconds after each setpoint.

        Returns:
            Ordered ``DCSweepReading`` list for each applied setpoint.

        Raises:
            ValueError: If ``step_v`` is zero.
            RuntimeError: If the adapter is not connected.
        """
        self._require_ps()
        if step_v == 0:
            raise ValueError("step_v must be non-zero.")

        ch = _normalize_ps_channel(channel)
        ilim = current_limit_a if current_limit_a is not None else self._ch_state[ch]["current_limit"]

        direction = 1.0 if stop_v >= start_v else -1.0
        abs_step = abs(step_v)
        epsilon = abs_step * 1e-9
        voltages: list[float] = []
        v = float(start_v)
        while direction * (v - stop_v) <= epsilon:
            voltages.append(round(v, 12))
            v += direction * abs_step
            if len(voltages) > 100_000:
                raise ValueError("Sweep would generate too many points.")
        if not voltages:
            voltages.append(float(start_v))

        _ps_configure_voltage(self._ps, ch, voltages[0], ilim)
        self._ps.enable_all_outputs(True)

        readings: list[DCSweepReading] = []
        for v_set in voltages:
            _ps_configure_voltage(self._ps, ch, v_set, ilim)
            if dwell_s > 0:
                time.sleep(dwell_s)

            try:
                v_meas, i_meas = _ps_read_output(self._ps, ch)
            except Exception:
                logger.warning("VB PS measurement failed at %.6g V — skipping", v_set)
                continue

            self._ch_state[ch]["voltage"] = v_set
            readings.append(
                DCSweepReading(
                    channel=ch,
                    voltage_setpoint_v=v_set,
                    current_limit_a=ilim,
                    measured_voltage_v=v_meas,
                    measured_current_a=i_meas,
                    metadata={"dwell_s": dwell_s, "backend": "virtualbench"},
                )
            )
            logger.debug("VB PS %s sweep: %.6g V → %.6g V, %.6g A", ch, v_set, v_meas, i_meas)

        return readings

    # ------------------------------------------------------------------
    # Extra VirtualBench-specific helpers
    # ------------------------------------------------------------------

    def enable_outputs(self, *, enabled: bool = True) -> None:
        """Enable or disable all PS rail outputs simultaneously.

        The VirtualBench PS does not support per-rail enable/disable;
        this method controls all three rails together.

        Args:
            enabled: ``True`` to enable, ``False`` to disable all outputs.
        """
        self._require_ps()
        self._ps.enable_all_outputs(enabled)
        logger.debug("VB PS outputs %s", "enabled" if enabled else "disabled")

    def measure_voltage(self, channel: InstrumentChannel) -> float:
        """Measure the actual output voltage for a PS rail.

        Args:
            channel: Rail identifier.

        Returns:
            Measured voltage in volts.
        """
        self._require_ps()
        v, _ = _ps_read_output(self._ps, _normalize_ps_channel(channel))
        return v

    def measure_current(self, channel: InstrumentChannel) -> float:
        """Measure the actual output current for a PS rail.

        Args:
            channel: Rail identifier.

        Returns:
            Measured current in amperes.
        """
        self._require_ps()
        _, i = _ps_read_output(self._ps, _normalize_ps_channel(channel))
        return i

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_ps(self) -> None:
        if self._ps is None:
            raise RuntimeError(
                f"VirtualBench PS adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )


__all__ = [
    "VirtualBenchOscilloscopeBackend",
    "VirtualBenchFGenBackend",
    "VirtualBenchPSBackend",
]
