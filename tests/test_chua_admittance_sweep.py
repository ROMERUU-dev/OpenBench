"""Tests for the ChuaAdmittanceSweep experiment."""

from __future__ import annotations

import pytest

from openbench.core.experiment import ExperimentState
from openbench.experiments import ChuaAdmittanceSweep as ExportedChuaAdmittanceSweep
from openbench.experiments.chua_admittance import (
    ChuaAdmittanceSweep,
    ChuaAdmittanceSweepConfig,
)


def test_chua_simulation_completes_and_returns_points() -> None:
    """Simulation mode sweeps bias and returns structured admittance data."""
    config = ChuaAdmittanceSweepConfig(
        bias_start_v=-2.0,
        bias_stop_v=2.0,
        bias_step_v=0.5,
    )
    result = ChuaAdmittanceSweep(
        name="chua-test",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.COMPLETED
    assert result.data["experiment"] == "chua_admittance_sweep"
    assert result.data["simulated"] is True
    assert result.data["point_count"] > 0
    assert len(result.data["points"]) == result.data["point_count"]


def test_chua_simulation_negative_resistance_regions() -> None:
    """Classic Chua parameters produce negative conductance in the inner region."""
    config = ChuaAdmittanceSweepConfig(
        bias_start_v=-2.0,
        bias_stop_v=2.0,
        bias_step_v=0.2,
        simulation_ga_s=-0.757e-3,
        simulation_gb_s=-0.409e-3,
        simulation_bp_v=1.0,
    )
    result = ChuaAdmittanceSweep(
        name="chua-neg-resistance",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.COMPLETED
    summary = result.data["summary"]
    assert summary["conductance_s_min"] < 0.0, "Inner region must show negative conductance"
    assert summary["negative_resistance_point_count"] > 0


def test_chua_simulation_admittance_breakpoint() -> None:
    """The summary detects the conductance breakpoint in the Chua characteristic."""
    config = ChuaAdmittanceSweepConfig(
        bias_start_v=-2.5,
        bias_stop_v=2.5,
        bias_step_v=0.1,
        simulation_ga_s=-0.757e-3,
        simulation_gb_s=-0.409e-3,
        simulation_bp_v=1.0,
    )
    result = ChuaAdmittanceSweep(
        name="chua-breakpoint",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.COMPLETED
    # Both Ga and Gb are negative so no sign reversal, but the conductance step
    # at ±Bp should be visible as a value change in the series.
    points = result.data["points"]
    conductances = [p["conductance_s"] for p in points]
    # All conductances negative (both Ga < 0 and Gb < 0)
    assert all(g < 0.0 for g in conductances)
    # Inner region (|V| < Bp) conductance is more negative than outer (Ga < Gb)
    inner = [p["conductance_s"] for p in points if abs(p["bias_voltage_v"]) < 0.9]
    outer = [p["conductance_s"] for p in points if abs(p["bias_voltage_v"]) > 1.1]
    if inner and outer:
        assert min(inner) < min(outer), "Inner conductance must be more negative than outer"


def test_chua_simulation_point_fields_are_finite() -> None:
    """All admittance point fields must be finite floats."""
    config = ChuaAdmittanceSweepConfig(
        bias_start_v=-1.5,
        bias_stop_v=1.5,
        bias_step_v=0.3,
    )
    result = ChuaAdmittanceSweep(
        name="chua-finite",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.COMPLETED
    import math

    float_fields = (
        "bias_voltage_v",
        "frequency_hz",
        "z_real_ohm",
        "z_imag_ohm",
        "magnitude_ohm",
        "phase_deg",
        "admittance_s",
        "conductance_s",
        "susceptance_s",
    )
    for point in result.data["points"]:
        for field_name in float_fields:
            value = point[field_name]
            assert math.isfinite(value), f"Field {field_name}={value} is not finite"


def test_chua_validation_rejects_zero_step() -> None:
    """Validation must reject bias_step_v <= 0."""
    config = ChuaAdmittanceSweepConfig(bias_step_v=0.0)
    result = ChuaAdmittanceSweep(name="chua-bad", config=config, simulate=True).run()
    assert result.state == ExperimentState.FAILED
    assert isinstance(result.error, ValueError)


def test_chua_validation_rejects_out_of_range_frequency() -> None:
    """Validation must reject frequencies outside SR860 operating range."""
    config = ChuaAdmittanceSweepConfig(ac_frequency_hz=1e9)
    result = ChuaAdmittanceSweep(name="chua-bad-freq", config=config, simulate=True).run()
    assert result.state == ExperimentState.FAILED
    assert isinstance(result.error, ValueError)


def test_chua_sweep_metadata_in_result() -> None:
    """Result must carry sweep metadata for reproducibility."""
    config = ChuaAdmittanceSweepConfig(
        bias_start_v=0.0,
        bias_stop_v=1.0,
        bias_step_v=0.5,
        ac_frequency_hz=2_000.0,
        excitation_v=0.05,
    )
    result = ChuaAdmittanceSweep(name="chua-meta", config=config, simulate=True).run()

    assert result.state == ExperimentState.COMPLETED
    sweep = result.data["sweep"]
    assert sweep["bias_start_v"] == pytest.approx(0.0)
    assert sweep["bias_stop_v"] == pytest.approx(1.0)
    assert sweep["ac_frequency_hz"] == pytest.approx(2_000.0)
    assert sweep["excitation_v"] == pytest.approx(0.05)


def test_chua_experiment_exported_from_experiments_package() -> None:
    """The public experiments namespace exposes ChuaAdmittanceSweep."""
    assert ExportedChuaAdmittanceSweep is ChuaAdmittanceSweep
