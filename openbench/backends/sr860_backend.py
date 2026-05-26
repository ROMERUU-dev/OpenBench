"""SR860/SR865 lock-in amplifier backend adapter for OpenBench.

Wraps ``SR860Controller`` from the standalone ``SRC_SR860_GUI`` project
(``~/SRC_SR860_GUI/barrido.py``).  Uses the same series-resistor divider
model as the original application to compute complex impedance from the
lock-in X/Y snapshot.

The impedance math (``impedance_from_series_divider`` and
``source_phasor_from_lockin_reference``) is inlined here to avoid importing
``barrido.py`` at module load time — that file imports Tkinter and Matplotlib
which are unavailable in headless CI environments.  ``SR860Controller`` itself
is imported lazily the first time a hardware connection is opened.

Measurement circuit topology::

    V_source (SINE OUT) → Z_source_series → R_series → DUT → GND
                                              ↑
                                      SR860 input A

Where ``Z_source_series`` is typically 50 Ω for single-ended output.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from openbench.core.interfaces import (
    IImpedanceAnalyzer,
    ImpedancePoint,
    InstrumentStatus,
)

logger = logging.getLogger(__name__)

# Candidate directory names for the SR860 project, tried in order.
# Linux installs often use "SRC_SR860_GUI"; Windows clones are typically
# named "sr860-impedance-workbench" (matching the GitHub repository name).
_LIB_CANDIDATES: list[Path] = [
    Path.home() / "SRC_SR860_GUI",
    Path.home() / "sr860-impedance-workbench",
]

# SR860 frequency limits (hardware spec)
_SR860_MIN_FREQ_HZ = 1e-3
_SR860_MAX_FREQ_HZ = 500_000.0

# SR860 OFLT time-constant codes → seconds
_TC_TABLE: list[float] = [
    1e-6, 3e-6, 10e-6, 30e-6, 100e-6, 300e-6,
    1e-3, 3e-3, 10e-3, 30e-3, 100e-3, 300e-3,
    1.0, 3.0, 10.0, 30.0, 100.0, 300.0,
    1_000.0, 3_000.0, 10_000.0, 30_000.0,
]


# ---------------------------------------------------------------------------
# Path / import helpers
# ---------------------------------------------------------------------------


def _find_lib_root() -> Path | None:
    """Return the first candidate directory that exists, or ``None``."""
    for candidate in _LIB_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


def _ensure_lib_on_path() -> bool:
    """Insert the SR860 source root into ``sys.path`` when present.

    Returns:
        ``True`` when a candidate directory exists and is on ``sys.path``.
    """
    lib_root = _find_lib_root()
    if lib_root is None:
        logger.warning(
            "SR860 library not found; tried: %s",
            ", ".join(str(p) for p in _LIB_CANDIDATES),
        )
        return False
    lib = str(lib_root)
    if lib in sys.path:
        return True
    sys.path.insert(0, lib)
    logger.debug("Added SR860 lib to sys.path: %s", lib)
    return True


def _import_sr860_controller() -> Any:
    """Import and return the ``SR860Controller`` class from barrido.

    Returns:
        ``SR860Controller`` class.

    Raises:
        ImportError: When the library directory is absent or the import fails.
    """
    if not _ensure_lib_on_path():
        candidates = ", ".join(str(p) for p in _LIB_CANDIDATES)
        raise ImportError(
            f"SR860 library not found; tried: {candidates}. "
            "Clone the project to one of those paths or use simulate=True."
        )
    from barrido import SR860Controller  # type: ignore[import]

    return SR860Controller


# ---------------------------------------------------------------------------
# Impedance math (inlined from barrido.py to avoid tkinter/matplotlib import)
# ---------------------------------------------------------------------------


def _source_phasor(magnitude_v: float, reference_phase_deg: float) -> complex:
    """Return the source voltage phasor in the lock-in X/Y reference frame.

    Mirrors ``source_phasor_from_lockin_reference`` from ``barrido.py``.
    ``PHAS`` rotates the internal reference; the SINE OUT follows the
    oscillator, so the source phasor carries phase ``−PHAS``.

    Args:
        magnitude_v: Effective source amplitude in volts RMS.
        reference_phase_deg: Lock-in reference phase (``PHAS``) in degrees.

    Returns:
        Complex source phasor in volts RMS.
    """
    phase_rad = math.radians(-reference_phase_deg)
    return magnitude_v * complex(math.cos(phase_rad), math.sin(phase_rad))


def _series_divider_impedance(
    series_ohm: float, source_v: complex, dut_v: complex
) -> complex:
    """Compute Z_DUT from the series-divider model.

    Mirrors ``impedance_from_series_divider`` from ``barrido.py``.

    Args:
        series_ohm: Total series reference impedance (R_series + Z_source) in ohms.
        source_v: Source voltage phasor in volts RMS.
        dut_v: Measured DUT voltage phasor (X + jY) in volts RMS.

    Returns:
        Complex impedance of the DUT in ohms.

    Raises:
        ZeroDivisionError: When the DUT voltage equals the source voltage
            (numerically unstable operating point).
    """
    denominator = source_v - dut_v
    if abs(denominator) < 1e-18:
        raise ZeroDivisionError(
            "DUT voltage ≈ source voltage — series-divider impedance is numerically unstable."
        )
    return series_ohm * dut_v / denominator


# ---------------------------------------------------------------------------
# Stub controller (simulation without hardware)
# ---------------------------------------------------------------------------


class _StubSR860Controller:
    """Minimal SR860 stub that returns realistic X/Y voltages from a model DUT.

    Default simulated component: 44.4 mH inductor with 10 Ω series resistance
    (matching the lab coil used in the Chua circuit characterisation workflow).
    Call ``_set_component`` to switch to a different component model.
    """

    def __init__(
        self,
        series_resistor_ohm: float,
        source_series_ohm: float,
        excitation_v: float,
    ) -> None:
        self._rs_total = series_resistor_ohm + source_series_ohm
        self._excitation_v = excitation_v
        self._freq_hz: float = 1_000.0
        self._phase_deg: float = 0.0
        # Default simulated DUT: 44.4 mH coil, 10 Ω DCR
        self._sim_r_ohm: float = 10.0
        self._sim_l_h: float = 44.4e-3
        self._sim_c_f: float = 0.0

    def require_connection(self) -> None:
        """No-op: stub is always "connected"."""

    def write(self, command: str) -> None:
        """Parse and store SCPI write commands that affect simulation state.

        Args:
            command: SCPI command string.
        """
        parts = command.strip().split(None, 1)
        if not parts:
            return
        cmd_upper = parts[0].upper()
        value_str = parts[1].strip() if len(parts) > 1 else ""
        try:
            if cmd_upper == "FREQ":
                self._freq_hz = float(value_str)
            elif cmd_upper == "SLVL":
                self._excitation_v = float(value_str)
            elif cmd_upper == "PHAS":
                self._phase_deg = float(value_str)
        except ValueError:
            pass

    def query(self, command: str) -> str:
        """Return simulated query responses.

        Args:
            command: SCPI query string.

        Returns:
            String response mimicking SR860 output.
        """
        cmd = command.strip().upper().rstrip("?")
        if cmd == "FREQ":
            return str(self._freq_hz)
        if cmd == "SLVL":
            return str(self._excitation_v)
        if cmd == "PHAS":
            return str(self._phase_deg)
        if cmd == "*IDN":
            return "Stanford_Research_Systems,SR860,SIM000000,1.0 (stub)"
        return "0"

    def read_snapshot_xy(self) -> tuple[float, float]:
        """Compute simulated X/Y voltages from the model DUT at current frequency.

        Returns:
            Tuple of ``(x_v, y_v)`` in volts RMS.
        """
        omega = 2.0 * math.pi * max(self._freq_hz, 1e-9)
        z_imag = omega * self._sim_l_h
        if self._sim_c_f > 0.0:
            z_imag -= 1.0 / (omega * self._sim_c_f)
        z_dut = complex(self._sim_r_ohm, z_imag)

        v_source = _source_phasor(self._excitation_v, self._phase_deg)
        denom = self._rs_total + z_dut
        v_dut = v_source * z_dut / denom if abs(denom) > 1e-30 else complex(0.0)

        rng = np.random.default_rng(int(self._freq_hz * 1e3) % (2**31))
        noise_scale = abs(v_dut) * 5e-4 if abs(v_dut) > 0 else 1e-9
        noise = noise_scale * (rng.random() - 0.5)
        return float(v_dut.real) + noise, float(v_dut.imag) + noise

    def close(self) -> None:
        """No-op: no hardware resources to release."""


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _build_impedance_point(
    x_v: float,
    y_v: float,
    frequency_hz: float,
    excitation_v: float,
    phase_deg: float,
    rs_total_ohm: float,
    backend_label: str = "sr860",
) -> ImpedancePoint:
    """Convert raw lock-in X/Y voltages to an ``ImpedancePoint``.

    Args:
        x_v: Lock-in X output in volts RMS.
        y_v: Lock-in Y output in volts RMS.
        frequency_hz: Stimulus frequency in hertz.
        excitation_v: Effective excitation amplitude in volts RMS.
        phase_deg: Lock-in reference phase (``PHAS``) in degrees.
        rs_total_ohm: Total series reference impedance in ohms.
        backend_label: Tag written to the metadata dict.

    Returns:
        Computed ``ImpedancePoint``.

    Raises:
        ZeroDivisionError: Propagated from ``_series_divider_impedance``.
    """
    source_v = _source_phasor(excitation_v, phase_deg)
    dut_v = complex(x_v, y_v)
    z_complex = _series_divider_impedance(rs_total_ohm, source_v, dut_v)
    return ImpedancePoint(
        frequency_hz=frequency_hz,
        z_real_ohm=z_complex.real,
        z_imag_ohm=z_complex.imag,
        phase_deg=math.degrees(math.atan2(z_complex.imag, z_complex.real)),
        magnitude_ohm=abs(z_complex),
        metadata={"x_v": x_v, "y_v": y_v, "backend": backend_label},
    )


# ---------------------------------------------------------------------------
# SR860Backend
# ---------------------------------------------------------------------------


@dataclass
class SR860Backend(IImpedanceAnalyzer):
    """OpenBench ``IImpedanceAnalyzer`` adapter for the Stanford Research SR860/SR865.

    Wraps ``SR860Controller`` from ``SRC_SR860_GUI/barrido.py`` using the same
    series-resistor divider model as the standalone application.

    Physical measurement topology::

        V_source (SINE OUT) → Z_source_series → R_series (known) → DUT → GND
                                                  ↑
                                          SR860 input A

    Attributes:
        name: Human-readable name registered with the orchestrator.
        resource: VISA resource string or ``/dev/usbtmcN`` raw path.  When
            ``None`` and ``simulate`` is ``False``, the first auto-discovered
            resource is used.
        simulate: When ``True``, a stub controller returns synthetic impedance
            data so experiments run without hardware.
        series_resistor_ohm: Known reference series resistor in ohms.
        source_series_ohm: Source/instrument series impedance in ohms.
            Default 50.0 corresponds to SR860 single-ended SINE OUT.
        excitation_v: Default SINE OUT amplitude sent via ``SLVL`` in volts RMS.
        time_constant_s: Lock-in filter time constant used for settling delay
            calculations.  Set programmatically with ``set_time_constant``.
        phase_deg: Lock-in reference phase (``PHAS``) in degrees.
    """

    series_resistor_ohm: float = 220.0
    source_series_ohm: float = 50.0
    excitation_v: float = 1.0
    time_constant_s: float = 0.1
    phase_deg: float = 0.0

    _controller: Any = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open the SR860 connection via the library controller."""
        SR860Controller = _import_sr860_controller()
        ctrl = SR860Controller()

        resource = self.resource or ""
        if not resource:
            try:
                available = ctrl.list_resources(refresh_session=False)
                if available:
                    resource = available[0]
                    logger.info("SR860 auto-discovered: %s", resource)
            except Exception:
                logger.debug("SR860 resource discovery failed", exc_info=True)

        if not resource:
            raise RuntimeError(
                "SR860 resource not specified and auto-discovery found nothing. "
                "Provide resource= or use simulate=True."
            )

        idn = ctrl.connect(resource)
        logger.info("SR860 IDN: %s", idn)

        # Push baseline configuration for raw X/Y measurements
        ctrl.write(f"SLVL {self.excitation_v}")
        ctrl.write(f"PHAS {self.phase_deg}")
        ctrl.write("RSRC 0")   # Internal reference oscillator
        ctrl.write("HARM 1")   # Detect fundamental
        # Disable display math so SNAP? X,Y returns raw RMS phasors
        for ch in ("X", "Y", "R"):
            ctrl.write(f"COFA {ch}, OFF")
            ctrl.write(f"CRAT {ch}, OFF")
            ctrl.write(f"CEXP {ch}, OFF")

        self._controller = ctrl

    def _disconnect(self) -> None:
        """Close the SR860 VISA/USBTMC session."""
        if self._controller is not None:
            try:
                self._controller.close()
            except Exception:
                logger.debug("Error closing SR860 connection", exc_info=True)
            self._controller = None

    def connect(self) -> None:  # type: ignore[override]
        """Connect to hardware or initialise the simulation stub.

        Overrides the base ``connect`` so that simulation mode populates
        ``_controller`` with a stub, keeping all measurement methods uniform.
        """
        if self._status in {InstrumentStatus.CONNECTED, InstrumentStatus.SIMULATED}:
            logger.debug("Instrument already connected: %s", self.name)
            return

        logger.info("Connecting instrument: %s", self.name)
        self._last_error = None

        if self.simulate:
            self._controller = _StubSR860Controller(
                series_resistor_ohm=self.series_resistor_ohm,
                source_series_ohm=self.source_series_ohm,
                excitation_v=self.excitation_v,
            )
            self._status = InstrumentStatus.SIMULATED
            logger.info("SR860 using simulation mode: %s", self.name)
            return

        try:
            self._connect()
        except Exception as exc:
            self._last_error = exc
            self._status = InstrumentStatus.ERROR
            logger.exception("SR860 connection failed: %s", self.name)
            raise

        self._status = InstrumentStatus.CONNECTED
        logger.info("SR860 connected: %s", self.name)

    # ------------------------------------------------------------------
    # IImpedanceAnalyzer interface
    # ------------------------------------------------------------------

    def measure_at_freq(
        self,
        frequency_hz: float,
        *,
        excitation_v: float | None = None,
        settle_periods: int = 5,
    ) -> ImpedancePoint:
        """Measure impedance at a single stimulus frequency.

        Sets the SR860 internal reference to ``frequency_hz``, waits for
        ``settle_periods × time_constant_s`` seconds, then reads X/Y and
        computes the complex impedance using the series-divider model.

        Args:
            frequency_hz: Stimulus frequency in hertz.
            excitation_v: Optional SINE OUT amplitude in volts RMS.  When
                ``None``, the current excitation level is kept unchanged.
            settle_periods: Number of ``time_constant_s`` periods to wait
                before sampling.  Typically 3–10.

        Returns:
            Measured impedance point.

        Raises:
            ValueError: If ``frequency_hz`` is outside the SR860 operating range.
            RuntimeError: If the adapter is not connected.
            ZeroDivisionError: If the DUT voltage equals the source voltage
                (degenerate measurement condition).
        """
        self._require_controller()

        if not (_SR860_MIN_FREQ_HZ <= frequency_hz <= _SR860_MAX_FREQ_HZ):
            raise ValueError(
                f"Frequency {frequency_hz} Hz is outside SR860 range "
                f"[{_SR860_MIN_FREQ_HZ}, {_SR860_MAX_FREQ_HZ}] Hz."
            )

        active_excitation = self.excitation_v
        if excitation_v is not None:
            self._controller.write(f"SLVL {excitation_v}")
            active_excitation = excitation_v

        self._controller.write(f"FREQ {frequency_hz}")

        settle_s = max(settle_periods * self.time_constant_s, 0.0)
        if settle_s > 0:
            time.sleep(settle_s)

        x_v, y_v = self._controller.read_snapshot_xy()

        try:
            phase_deg = float(self._controller.query("PHAS?"))
        except Exception:
            phase_deg = self.phase_deg

        rs_total = self.series_resistor_ohm + self.source_series_ohm
        point = _build_impedance_point(
            x_v, y_v, frequency_hz, active_excitation, phase_deg, rs_total
        )
        logger.debug(
            "SR860 measure_at_freq %.6g Hz → |Z|=%.6g Ω, φ=%.3g°",
            frequency_hz,
            point.magnitude_ohm,
            point.phase_deg,
        )
        return point

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
            excitation_v: Optional SINE OUT amplitude in volts RMS.  Applied
                at the first point only; subsequent points reuse the level.
            log_scale: When ``True``, points are distributed on a log10 scale
                (recommended for wide-band sweeps).
            settle_periods: Number of ``time_constant_s`` periods to wait per
                frequency step.

        Returns:
            Ordered impedance measurements for each stimulus frequency.
            Points where the impedance calculation is numerically degenerate
            are silently skipped (logged at WARNING level).

        Raises:
            ValueError: If sweep parameters are invalid or outside SR860 range.
            RuntimeError: If the adapter is not connected.
        """
        self._require_controller()

        if num_points < 2:
            raise ValueError("num_points must be >= 2.")
        if start_hz <= 0 or stop_hz <= 0:
            raise ValueError("Frequencies must be positive.")
        if stop_hz <= start_hz:
            raise ValueError("stop_hz must be greater than start_hz.")

        frequencies = (
            np.geomspace(start_hz, stop_hz, num_points)
            if log_scale
            else np.linspace(start_hz, stop_hz, num_points)
        )

        points: list[ImpedancePoint] = []
        for i, freq in enumerate(frequencies):
            freq_f = float(freq)
            # Apply excitation override only at the first point
            exc_override = excitation_v if i == 0 else None
            try:
                point = self.measure_at_freq(
                    freq_f,
                    excitation_v=exc_override,
                    settle_periods=settle_periods,
                )
                points.append(point)
                logger.debug(
                    "SR860 sweep %d/%d: %.6g Hz → |Z|=%.6g Ω",
                    i + 1,
                    num_points,
                    freq_f,
                    point.magnitude_ohm,
                )
            except (ValueError, ZeroDivisionError) as exc:
                logger.warning(
                    "SR860 sweep point %.6g Hz skipped: %s", freq_f, exc
                )

        return points

    # ------------------------------------------------------------------
    # SR860-specific public helpers
    # ------------------------------------------------------------------

    def identify(self) -> str:
        """Query and return the instrument identification string.

        Returns:
            IDN string from ``*IDN?``.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_controller()
        return self._controller.query("*IDN?")

    def set_excitation(self, amplitude_v: float) -> None:
        """Set the SINE OUT amplitude (``SLVL``) and update the stored default.

        Args:
            amplitude_v: Excitation amplitude in volts RMS.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_controller()
        self._controller.write(f"SLVL {amplitude_v}")
        self.excitation_v = amplitude_v
        logger.debug("SR860 SLVL → %.6g V", amplitude_v)

    def set_time_constant(self, tc_s: float) -> None:
        """Set the lock-in filter time constant to the closest available value.

        The SR860 uses an enumerated list of time constants.  This method
        selects the nearest code and updates ``time_constant_s`` accordingly.

        Args:
            tc_s: Desired time constant in seconds.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_controller()
        code = min(range(len(_TC_TABLE)), key=lambda i: abs(_TC_TABLE[i] - tc_s))
        self._controller.write(f"OFLT {code}")
        self.time_constant_s = _TC_TABLE[code]
        logger.debug("SR860 OFLT code %d → %.6g s", code, self.time_constant_s)

    def read_xy(self) -> tuple[float, float]:
        """Read the current X/Y voltages from the lock-in.

        Returns:
            Tuple of ``(x_v, y_v)`` in volts RMS.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        self._require_controller()
        return self._controller.read_snapshot_xy()

    def set_sim_component(
        self,
        r_ohm: float,
        *,
        l_h: float = 0.0,
        c_f: float = 0.0,
    ) -> None:
        """Configure the simulated DUT for simulation mode.

        Supports series R, RL, RC, and RLC combinations.
        This is a no-op when connected to real hardware.

        Args:
            r_ohm: Series resistance in ohms.
            l_h: Inductance in henrys.
            c_f: Capacitance in farads.
        """
        if not self.simulate or not isinstance(self._controller, _StubSR860Controller):
            return
        self._controller._sim_r_ohm = r_ohm
        self._controller._sim_l_h = l_h
        self._controller._sim_c_f = c_f
        logger.debug(
            "SR860 stub DUT updated: R=%.6g Ω, L=%.6g H, C=%.6g F", r_ohm, l_h, c_f
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_controller(self) -> None:
        if self._controller is None:
            raise RuntimeError(
                f"SR860 adapter '{self.name}' is not connected. "
                "Call connect() or use it as a context manager."
            )


__all__ = ["SR860Backend"]
