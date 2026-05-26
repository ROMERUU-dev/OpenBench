"""Keysight E36312A backend adapter for OpenBench.

Wraps the standalone ``keysight_E36312A_DCSweep`` project without
reimplementing SCPI communication. The underlying driver is imported lazily
the first time a connection is opened so OpenBench can be imported on machines
where the library path does not exist.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbench.core.interfaces import DCSweepReading, IDCSupply, InstrumentChannel

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_LIB_ROOT = Path.home() / "keysight_E36312A_DCSweep"
_CHANNELS = ("CH1", "CH2", "CH3")


def _ensure_lib_on_path() -> bool:
    """Insert the keysight library root into ``sys.path`` when present.

    Returns:
        ``True`` when the library directory exists and is on ``sys.path``.
    """
    lib = str(_LIB_ROOT)
    if lib in sys.path:
        return True
    if _LIB_ROOT.is_dir():
        sys.path.insert(0, lib)
        logger.debug("Added keysight library to sys.path: %s", lib)
        return True
    logger.warning("keysight_E36312A_DCSweep not found at %s", _LIB_ROOT)
    return False


def _import_keysight() -> Any:
    """Import and return the ``KeysightSupply`` class from the backend library.

    Raises:
        ImportError: When the library directory is absent or the import fails.
    """
    if not _ensure_lib_on_path():
        raise ImportError(
            f"keysight_E36312A_DCSweep library not found at {_LIB_ROOT}. "
            "Clone the project there or set simulate=True."
        )
    from src.instruments.keysight_supply import KeysightSupply  # type: ignore[import]

    return KeysightSupply


def _import_sweep_helpers() -> tuple[Any, Any]:
    """Import sweep utilities from the backend library.

    Returns:
        Tuple of ``(generate_sweep_values, is_in_compliance)``.
    """
    if not _ensure_lib_on_path():
        raise ImportError("keysight_E36312A_DCSweep library not found.")
    from src.measurements.dc_sweep import generate_sweep_values, is_in_compliance  # type: ignore[import]

    return generate_sweep_values, is_in_compliance


def _normalize_channel(channel: InstrumentChannel) -> str:
    """Convert an OpenBench channel identifier to a Keysight channel label.

    Args:
        channel: Integer (1–3) or string (``"CH1"``–``"CH3"``, or ``"1"``–``"3"``).

    Returns:
        Normalized label such as ``"CH1"``.

    Raises:
        ValueError: If the channel is not in the valid range.
    """
    if isinstance(channel, int):
        label = f"CH{channel}"
    else:
        raw = str(channel).strip().upper()
        label = raw if raw.startswith("CH") else f"CH{raw}"
    if label not in _CHANNELS:
        raise ValueError(
            f"Invalid Keysight channel {channel!r}; expected 1–3 or CH1–CH3."
        )
    return label


@dataclass
class KeysightE36312ABackend(IDCSupply):
    """OpenBench ``IDCSupply`` adapter for the Keysight E36312A.

    Wraps ``KeysightSupply`` from the standalone ``keysight_E36312A_DCSweep``
    project. Simulation mode uses the library's own ``MockVisaResource`` so
    mock behaviour is consistent with the standalone application.

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: VISA resource string (e.g. ``"USB0::0x2A8D::...::INSTR"``).
            When ``None`` and ``simulate`` is ``True``, the mock resource is
            used automatically.
        simulate: When ``True``, the adapter uses the library's built-in mock
            instead of opening a hardware connection.
        compliance_tolerance: Fractional tolerance for compliance detection
            during sweeps (default 0.02 = 2 %).
    """

    compliance_tolerance: float = 0.02
    _supply: Any = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Connection lifecycle hooks
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open the Keysight driver against the configured VISA resource."""
        KeysightSupply = _import_keysight()
        self._supply = KeysightSupply(
            resource_name=self.resource,
            mock=False,
        )
        self._supply.connect(self.resource)
        idn = self._supply.identify()
        logger.info("Keysight IDN: %s", idn)

    def _disconnect(self) -> None:
        """Run a safe shutdown then close the VISA session."""
        if self._supply is not None:
            try:
                self._supply.safe_shutdown(close=True)
            except Exception:
                logger.exception("Error during Keysight safe shutdown")
            self._supply = None

    # ------------------------------------------------------------------
    # Simulation: override connect so the mock is instantiated directly
    # ------------------------------------------------------------------

    def connect(self) -> None:  # type: ignore[override]
        """Connect using the library mock when simulation mode is active.

        The override is needed because ``IInstrument.connect`` skips
        ``_connect`` entirely in simulate mode, but we want the mock driver
        object available for the other methods to call.
        """
        from openbench.core.interfaces import InstrumentStatus

        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            try:
                KeysightSupply = _import_keysight()
                self._supply = KeysightSupply(mock=True)
                self._supply.connect()
            except ImportError:
                logger.debug(
                    "keysight library absent in simulate mode — using stub supply for %s",
                    self.name,
                )
                self._supply = _StubSupply()
            self._status = InstrumentStatus.SIMULATED
            logger.info("Keysight adapter using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("Keysight connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("Keysight adapter connected: %s", self.name)

    # ------------------------------------------------------------------
    # IDCSupply interface
    # ------------------------------------------------------------------

    def set_voltage(self, channel: InstrumentChannel, voltage_v: float) -> None:
        """Set the output voltage for a Keysight supply channel.

        Args:
            channel: Channel identifier — integer 1–3 or string ``"CH1"``–``"CH3"``.
            voltage_v: Voltage setpoint in volts.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the channel or voltage is outside safety limits.
        """
        self._require_supply()
        ch = _normalize_channel(channel)
        logger.debug("set_voltage %s → %.6g V", ch, voltage_v)
        self._supply.set_voltage(ch, voltage_v)

    def set_current(self, channel: InstrumentChannel, current_a: float) -> None:
        """Set the current compliance limit for a Keysight supply channel.

        Args:
            channel: Channel identifier — integer 1–3 or string ``"CH1"``–``"CH3"``.
            current_a: Current compliance limit in amperes.

        Raises:
            RuntimeError: If the adapter is not connected.
            ValueError: If the channel or current is outside safety limits.
        """
        self._require_supply()
        ch = _normalize_channel(channel)
        logger.debug("set_current %s → %.6g A", ch, current_a)
        self._supply.set_current_limit(ch, current_a)

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
        """Sweep a Keysight channel and return measured readings.

        The channel output is enabled before the sweep and left in its final
        state (at ``stop_v``) after completion; callers should set voltage to
        zero or call ``set_voltage`` as needed.

        Args:
            channel: Channel identifier — integer 1–3 or string ``"CH1"``–``"CH3"``.
            start_v: First voltage setpoint in volts.
            stop_v: Final voltage boundary in volts.
            step_v: Voltage step magnitude. Sign is ignored; direction is
                inferred from ``start_v`` and ``stop_v``.
            current_limit_a: Current compliance limit in amperes to apply
                before the sweep. When ``None``, the channel's existing limit
                is kept.
            dwell_s: Settling time in seconds after each voltage step.

        Returns:
            Ordered ``DCSweepReading`` list for each applied setpoint.

        Raises:
            ValueError: If sweep parameters are invalid.
            RuntimeError: If the adapter is not connected.
        """
        self._require_supply()
        ch = _normalize_channel(channel)

        if step_v == 0:
            raise ValueError("step_v must be non-zero")

        try:
            generate_sweep_values, is_in_compliance = _import_sweep_helpers()
        except ImportError:
            generate_sweep_values = _fallback_sweep_values
            is_in_compliance = _fallback_compliance

        voltages = generate_sweep_values(start_v, stop_v, abs(step_v) * (1 if stop_v >= start_v else -1))

        if current_limit_a is not None:
            self._supply.set_current_limit(ch, current_limit_a)
            ilim = current_limit_a
        else:
            try:
                ilim = self._supply.query_current_limit(ch)
            except Exception:
                ilim = None

        if voltages:
            self._supply.set_voltage(ch, voltages[0])
        self._supply.output_on(ch)

        readings: list[DCSweepReading] = []
        for v_set in voltages:
            self._supply.set_voltage(ch, v_set)
            if dwell_s:
                time.sleep(dwell_s)

            try:
                v_meas = self._supply.measure_voltage(ch)
                i_meas = self._supply.measure_current(ch)
            except Exception:
                logger.warning("Keysight measurement failed at %.6g V — skipping point", v_set)
                continue

            compliance = False
            if ilim is not None:
                try:
                    compliance = is_in_compliance(i_meas, ilim, self.compliance_tolerance)
                except Exception:
                    pass

            readings.append(
                DCSweepReading(
                    channel=ch,
                    voltage_setpoint_v=v_set,
                    current_limit_a=ilim,
                    measured_voltage_v=v_meas,
                    measured_current_a=i_meas,
                    metadata={"compliance": compliance, "dwell_s": dwell_s},
                )
            )

            if compliance:
                logger.info(
                    "Keysight %s compliance at %.6g V (%.6g A) — stopping sweep",
                    ch,
                    v_set,
                    i_meas,
                )
                break

        return readings

    # ------------------------------------------------------------------
    # Extra Keysight-specific helpers (exposed for experiment use)
    # ------------------------------------------------------------------

    def enable_output(self, channel: InstrumentChannel, *, enabled: bool = True) -> None:
        """Enable or disable the output for a channel.

        Args:
            channel: Channel identifier.
            enabled: ``True`` to turn output on, ``False`` to turn it off.
        """
        self._require_supply()
        ch = _normalize_channel(channel)
        if enabled:
            self._supply.output_on(ch)
        else:
            self._supply.output_off(ch)
        logger.debug("Output %s → %s", ch, "ON" if enabled else "OFF")

    def measure_voltage(self, channel: InstrumentChannel) -> float:
        """Measure and return the output voltage for a channel.

        Args:
            channel: Channel identifier.

        Returns:
            Measured voltage in volts.
        """
        self._require_supply()
        return float(self._supply.measure_voltage(_normalize_channel(channel)))

    def measure_current(self, channel: InstrumentChannel) -> float:
        """Measure and return the output current for a channel.

        Args:
            channel: Channel identifier.

        Returns:
            Measured current in amperes.
        """
        self._require_supply()
        return float(self._supply.measure_current(_normalize_channel(channel)))

    def safe_shutdown(self) -> None:
        """Ramp all channels to 0 V, disable outputs, and release the session."""
        if self._supply is not None:
            self._supply.safe_shutdown(close=True)
            self._supply = None

    def set_mock_model(self, model: str) -> None:
        """Set the DUT model for the built-in mock (``"resistor"``, ``"diode"``, ``"nmos"``).

        This is a no-op when connected to real hardware.

        Args:
            model: Mock model name recognized by the library's ``MockVisaResource``.
        """
        if self._supply is not None and self.simulate:
            if hasattr(self._supply, "set_mock_model"):
                self._supply.set_mock_model(model)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_supply(self) -> None:
        if self._supply is None:
            raise RuntimeError(
                f"Keysight adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )


# ---------------------------------------------------------------------------
# Pure-Python fallbacks used when the library is absent in simulate mode
# ---------------------------------------------------------------------------


def _fallback_sweep_values(start: float, stop: float, step: float) -> list[float]:
    """Minimal sweep generator used when the keysight library is unavailable."""
    if step == 0:
        raise ValueError("step must be non-zero")
    direction = 1.0 if stop >= start else -1.0
    signed_step = direction * abs(step)
    epsilon = abs(step) * 1e-9
    values: list[float] = []
    v = float(start)
    while direction * (v - stop) <= epsilon:
        values.append(round(v, 12))
        v += signed_step
        if len(values) > 100_000:
            raise ValueError("Sweep would generate too many points")
    if not values:
        values.append(round(float(start), 12))
    if abs(values[-1] - stop) > epsilon:
        values.append(round(float(stop), 12))
    return values


def _fallback_compliance(current_a: float, limit_a: float, tolerance: float) -> bool:
    """Compliance check used when the keysight library is unavailable."""
    if limit_a <= 0:
        return False
    return abs(current_a) >= abs(limit_a) * (1.0 - max(0.0, min(0.95, tolerance)))


class _StubSupply:
    """Minimal in-process stub for when the keysight library is absent."""

    def __init__(self) -> None:
        self._channels: dict[str, dict[str, float]] = {
            ch: {"voltage": 0.0, "current_limit": 0.1, "on": 0.0}
            for ch in _CHANNELS
        }

    def set_voltage(self, channel: str, voltage: float) -> None:
        self._channels[channel]["voltage"] = voltage

    def set_current_limit(self, channel: str, current: float) -> None:
        self._channels[channel]["current_limit"] = current

    def output_on(self, channel: str) -> None:
        self._channels[channel]["on"] = 1.0

    def output_off(self, channel: str) -> None:
        self._channels[channel]["on"] = 0.0

    def measure_voltage(self, channel: str) -> float:
        state = self._channels[channel]
        return state["voltage"] if state["on"] else 0.0

    def measure_current(self, channel: str) -> float:
        state = self._channels[channel]
        if not state["on"]:
            return 0.0
        load = 1000.0
        ideal = state["voltage"] / load
        return min(ideal, state["current_limit"])

    def query_current_limit(self, channel: str) -> float:
        return self._channels[channel]["current_limit"]

    def identify(self) -> str:
        return "STUB,E36312A,SIM0000,0.0"

    def safe_shutdown(self, *, close: bool = False) -> None:
        for ch in _CHANNELS:
            self._channels[ch]["voltage"] = 0.0
            self._channels[ch]["on"] = 0.0

    def set_mock_model(self, model: str) -> None:
        pass


__all__ = ["KeysightE36312ABackend"]
