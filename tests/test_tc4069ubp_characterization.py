"""Tests for the TC4069UBP characterization experiment."""

from __future__ import annotations

from openbench.core.experiment import ExperimentState
from openbench.experiments.component_char import (
    TC4069UBPCharacterization,
    TC4069UBPCharacterizationConfig,
)


def test_tc4069ubp_simulation_returns_transfer_curve() -> None:
    """Simulation mode produces a complete inverter transfer sweep."""
    config = TC4069UBPCharacterizationConfig(
        input_start_v=0.0,
        input_stop_v=5.0,
        input_step_v=1.0,
    )
    result = TC4069UBPCharacterization(
        name="tc4069ubp",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.COMPLETED
    assert result.data["component"] == "TC4069UBP"
    assert result.data["point_count"] == 6
    assert result.data["simulated"] is True
    assert (
        result.data["points"][0]["output_voltage_v"]
        > result.data["points"][-1]["output_voltage_v"]
    )
    assert result.data["switching_threshold_v"] is not None


def test_tc4069ubp_validation_rejects_wrong_step_direction() -> None:
    """Ascending sweeps require a positive input step."""
    config = TC4069UBPCharacterizationConfig(
        input_start_v=0.0,
        input_stop_v=5.0,
        input_step_v=-0.5,
    )
    result = TC4069UBPCharacterization(
        name="tc4069ubp",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.FAILED
    assert isinstance(result.error, ValueError)


def test_tc4069ubp_descending_simulation_sweep() -> None:
    """Descending sweeps are supported when the step sign matches."""
    config = TC4069UBPCharacterizationConfig(
        input_start_v=5.0,
        input_stop_v=0.0,
        input_step_v=-2.5,
    )
    result = TC4069UBPCharacterization(
        name="tc4069ubp",
        config=config,
        simulate=True,
    ).run()

    inputs = [point["input_voltage_v"] for point in result.data["points"]]
    assert result.state == ExperimentState.COMPLETED
    assert inputs == [5.0, 2.5, 0.0]
