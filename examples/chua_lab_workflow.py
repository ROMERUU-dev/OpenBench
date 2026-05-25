"""Chua circuit Monday lab workflow — three automated measurements.

Execute this script on the lab bench to run the full Chua circuit
characterization session in sequence:

    1. TC4069UBP transfer characterization (Keysight DC + VirtualBench scope)
    2. Chua inductor frequency sweep (SR860 impedance sweep)
    3. Chua admittance vs. DC bias sweep (SR860 + Keysight bias)

Usage::

    # Simulation (development / dry-run, no hardware required):
    python chua_lab_workflow.py --simulate

    # Full hardware run:
    python chua_lab_workflow.py

    # Run only a subset of experiments:
    python chua_lab_workflow.py --only tc4069 inductor

    # Save results to a custom directory:
    python chua_lab_workflow.py --simulate --output-dir /tmp/chua_results

    # Verbose logging:
    python chua_lab_workflow.py --simulate -v

Results are saved as JSON in the output directory (default: ./chua_results/).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Experiment imports — lazy-safe because backends guard hardware access
# ---------------------------------------------------------------------------

from openbench.core.experiment import ExperimentResult, ExperimentState
from openbench.experiments.chua_admittance import (
    ChuaAdmittanceSweep,
    ChuaAdmittanceSweepConfig,
)
from openbench.experiments.component_char import (
    InductorCharacterization,
    InductorCharacterizationConfig,
    TC4069UBPCharacterization,
    TC4069UBPCharacterizationConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lab-specific defaults (override via CLI flags or by editing this section)
# ---------------------------------------------------------------------------

_DEFAULT_TC4069_CONFIG = TC4069UBPCharacterizationConfig(
    supply_voltage_v=5.0,
    input_start_v=0.0,
    input_stop_v=5.0,
    input_step_v=0.1,
    current_limit_a=0.02,
    vdd_channel="CH1",
    input_channel="CH2",
    scope_output_channel=1,
    dwell_s=0.02,
)

_DEFAULT_INDUCTOR_CONFIG = InductorCharacterizationConfig(
    start_hz=100.0,
    stop_hz=100_000.0,
    num_points=30,
    excitation_v=1.0,
    log_scale=True,
    settle_periods=5,
    time_constant_s=0.1,
    series_resistor_ohm=220.0,
    source_series_ohm=50.0,
    nominal_inductance_h=44.4e-3,
    simulation_inductance_h=44.4e-3,
    simulation_series_resistance_ohm=10.0,
)

_DEFAULT_CHUA_ADMITTANCE_CONFIG = ChuaAdmittanceSweepConfig(
    bias_start_v=-2.0,
    bias_stop_v=2.0,
    bias_step_v=0.1,
    bias_current_limit_a=0.05,
    bias_channel="CH1",
    ac_frequency_hz=1_000.0,
    excitation_v=0.1,
    settle_s=0.05,
    settle_periods=5,
    time_constant_s=0.1,
    series_resistor_ohm=220.0,
    source_series_ohm=50.0,
)

_EXPERIMENT_KEYS = ("tc4069", "inductor", "chua")


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


def _make_progress_callback(name: str) -> Any:
    """Return a callback that logs progress for an experiment.

    Args:
        name: Experiment display name used in log messages.

    Returns:
        Callable accepting ``(message, fraction)`` compatible with
        ``BaseExperiment.on_progress``.
    """

    def _cb(message: str, fraction: float) -> None:
        bar_width = 30
        filled = int(bar_width * fraction)
        bar = "#" * filled + "-" * (bar_width - filled)
        pct = int(fraction * 100)
        logger.info("[%s] [%s] %3d%% — %s", name, bar, pct, message)

    return _cb


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


def _save_result(result: ExperimentResult, output_dir: Path, tag: str) -> Path:
    """Serialise an ExperimentResult to a timestamped JSON file.

    Args:
        result: Completed (or failed) experiment result.
        output_dir: Directory into which the file is written. Created if absent.
        tag: Short label used in the filename (e.g. ``"tc4069"``).

    Returns:
        Path to the written JSON file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = f"{ts}_{tag}_{result.state}.json"
    path = output_dir / filename

    payload: dict[str, Any] = {
        "experiment": result.name,
        "state": result.state,
        "duration_s": result.duration_s,
        "error": str(result.error) if result.error else None,
        "data": dict(result.data),
    }

    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Result saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# Individual experiment runners
# ---------------------------------------------------------------------------


def run_tc4069_characterization(
    *,
    simulate: bool,
    output_dir: Path,
) -> ExperimentResult:
    """Run the TC4069UBP CMOS inverter transfer characterization.

    Uses Keysight E36312A as DC supply and VirtualBench as oscilloscope.
    In simulation mode, synthetic transfer data is produced without hardware.

    Args:
        simulate: When True, run without hardware.
        output_dir: Directory for the persisted JSON result.

    Returns:
        ExperimentResult with component, transfer points, and threshold.
    """
    logger.info("=== Experiment 1/3: TC4069UBP Transfer Characterization ===")

    exp = TC4069UBPCharacterization(
        name="tc4069-transfer",
        config=_DEFAULT_TC4069_CONFIG,
        simulate=simulate,
        on_progress=_make_progress_callback("TC4069UBP"),
    )
    result = exp.run()

    if result.state == ExperimentState.COMPLETED:
        data = result.data
        logger.info(
            "TC4069UBP: %d points, threshold=%.3f V, supply=%.2f V",
            data.get("point_count", 0),
            data.get("switching_threshold_v") or float("nan"),
            data.get("supply_voltage_v", 0.0),
        )
    else:
        logger.error("TC4069UBP characterization %s: %s", result.state, result.error)

    _save_result(result, output_dir, "tc4069")
    return result


def run_inductor_characterization(
    *,
    simulate: bool,
    output_dir: Path,
) -> ExperimentResult:
    """Run the Chua inductor impedance characterization via SR860 sweep.

    Sweeps from 100 Hz to 100 kHz (log spacing) and derives inductance,
    ESR, and quality factor at each frequency. In simulation mode the
    SR860 backend generates data from an ideal RL model.

    Args:
        simulate: When True, run without hardware.
        output_dir: Directory for the persisted JSON result.

    Returns:
        ExperimentResult with per-point impedance and inductor summary.
    """
    logger.info("=== Experiment 2/3: Inductor Characterization (SR860) ===")

    exp = InductorCharacterization(
        name="chua-inductor",
        config=_DEFAULT_INDUCTOR_CONFIG,
        simulate=simulate,
        on_progress=_make_progress_callback("Inductor"),
    )
    result = exp.run()

    if result.state == ExperimentState.COMPLETED:
        summary = result.data.get("summary", {})
        inductance_mh = (summary.get("inductance_h_median") or 0.0) * 1e3
        esr = summary.get("series_resistance_ohm_median") or 0.0
        q = summary.get("quality_factor_mean")
        srf = summary.get("self_resonance_hz")
        logger.info(
            "Inductor: L_median=%.2f mH, ESR=%.1f Ω, Q_mean=%s, SRF=%s Hz",
            inductance_mh,
            esr,
            f"{q:.1f}" if q is not None else "N/A",
            f"{srf:.0f}" if srf is not None else "N/A",
        )
        error_pct = summary.get("nominal_error_percent")
        if error_pct is not None:
            logger.info("Inductor vs. nominal (44.4 mH): %+.1f%%", error_pct)
    else:
        logger.error("Inductor characterization %s: %s", result.state, result.error)

    _save_result(result, output_dir, "inductor")
    return result


def run_chua_admittance_sweep(
    *,
    simulate: bool,
    output_dir: Path,
) -> ExperimentResult:
    """Run the Chua nonlinear element admittance vs. DC bias sweep.

    Applies DC bias setpoints via Keysight E36312A and measures
    small-signal AC impedance with the SR860 lock-in at each point.
    Admittance Y = 1/Z is derived, tracing the N-shaped conductance
    characteristic of the Chua diode.

    Args:
        simulate: When True, run without hardware using the piecewise-linear
            Chua diode model.
        output_dir: Directory for the persisted JSON result.

    Returns:
        ExperimentResult with per-bias admittance points and summary.
    """
    logger.info("=== Experiment 3/3: Chua Admittance vs. DC Bias (SR860 + Keysight) ===")

    exp = ChuaAdmittanceSweep(
        name="chua-admittance",
        config=_DEFAULT_CHUA_ADMITTANCE_CONFIG,
        simulate=simulate,
        on_progress=_make_progress_callback("ChuaAdmittance"),
    )
    result = exp.run()

    if result.state == ExperimentState.COMPLETED:
        summary = result.data.get("summary", {})
        g_min_ms = (summary.get("conductance_s_min") or 0.0) * 1e3
        g_max_ms = (summary.get("conductance_s_max") or 0.0) * 1e3
        neg_pts = summary.get("negative_resistance_point_count", 0)
        bp_detected = summary.get("breakpoint_detected", False)
        logger.info(
            "Chua admittance: G_min=%.3f mS, G_max=%.3f mS, "
            "neg-resistance points=%d, breakpoint_detected=%s",
            g_min_ms,
            g_max_ms,
            neg_pts,
            bp_detected,
        )
    else:
        logger.error("Chua admittance sweep %s: %s", result.state, result.error)

    _save_result(result, output_dir, "chua_admittance")
    return result


# ---------------------------------------------------------------------------
# Workflow orchestration
# ---------------------------------------------------------------------------


def run_workflow(
    *,
    simulate: bool,
    only: list[str] | None,
    output_dir: Path,
) -> dict[str, ExperimentResult]:
    """Run the full Chua Monday lab measurement sequence.

    Experiments run in a fixed order that matches the physical lab setup.
    Any experiment not in ``only`` is skipped. Results are collected and
    returned regardless of individual experiment success or failure — the
    workflow never aborts on a single experiment failure.

    Args:
        simulate: When True, all experiments run without hardware.
        only: Whitelist of experiment keys (``"tc4069"``, ``"inductor"``,
            ``"chua"``). When empty or None, all experiments run.
        output_dir: Base directory for JSON result files.

    Returns:
        Mapping from experiment key to its ``ExperimentResult``.
    """
    active = set(only) if only else set(_EXPERIMENT_KEYS)
    results: dict[str, ExperimentResult] = {}

    mode_label = "SIMULATION" if simulate else "HARDWARE"
    logger.info(
        "========================================\n"
        " Chua Lab Workflow — %s MODE\n"
        " Started: %s\n"
        " Active: %s\n"
        "========================================",
        mode_label,
        datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        ", ".join(sorted(active)),
    )

    t0 = time.monotonic()

    if "tc4069" in active:
        results["tc4069"] = run_tc4069_characterization(
            simulate=simulate, output_dir=output_dir
        )

    if "inductor" in active:
        results["inductor"] = run_inductor_characterization(
            simulate=simulate, output_dir=output_dir
        )

    if "chua" in active:
        results["chua"] = run_chua_admittance_sweep(
            simulate=simulate, output_dir=output_dir
        )

    elapsed = time.monotonic() - t0
    _log_workflow_summary(results, elapsed)
    return results


def _log_workflow_summary(
    results: dict[str, ExperimentResult], elapsed_s: float
) -> None:
    """Log a one-table summary of all experiment outcomes.

    Args:
        results: Mapping from experiment key to result.
        elapsed_s: Total wall-clock time for the workflow in seconds.
    """
    logger.info("========================================")
    logger.info(" Workflow Summary — %.1f s total", elapsed_s)
    logger.info("========================================")

    passed = 0
    failed = 0
    for key, result in results.items():
        ok = result.state == ExperimentState.COMPLETED
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        logger.info(
            "  %-12s  [%s]  %.2f s%s",
            key,
            status,
            result.duration_s,
            f"  — {result.error}" if result.error else "",
        )

    logger.info("  %-12s  %d/%d passed", "TOTAL", passed, passed + failed)
    logger.info("========================================")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        default=False,
        help="Run in simulation mode (no hardware required).",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=list(_EXPERIMENT_KEYS),
        metavar="EXPERIMENT",
        help=(
            f"Run only the specified experiment(s). Choices: "
            f"{', '.join(_EXPERIMENT_KEYS)}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("chua_results"),
        help="Directory for JSON result files (default: ./chua_results/).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)
    if not verbose:
        logging.getLogger("openbench.backends").setLevel(logging.WARNING)


def main() -> int:
    """CLI entry point.

    Returns:
        Exit code — 0 if all experiments succeeded, 1 if any failed.
    """
    parser = _build_parser()
    args = parser.parse_args()

    _configure_logging(args.verbose)

    results = run_workflow(
        simulate=args.simulate,
        only=args.only,
        output_dir=args.output_dir,
    )

    all_ok = all(r.state == ExperimentState.COMPLETED for r in results.values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
