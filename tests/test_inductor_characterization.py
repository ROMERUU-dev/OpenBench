"""Tests for the SR860 inductor characterization experiment."""

from __future__ import annotations

import pytest

from openbench.core.experiment import ExperimentState
from openbench.experiments import InductorCharacterization as ExportedInductorCharacterization
from openbench.experiments.component_char import (
    InductorCharacterization,
    InductorCharacterizationConfig,
)


def test_inductor_simulation_returns_characterization_summary() -> None:
    """Simulation mode sweeps the SR860 backend and derives inductor metrics."""
    config = InductorCharacterizationConfig(
        start_hz=100.0,
        stop_hz=10_000.0,
        num_points=6,
        nominal_inductance_h=44.4e-3,
    )
    result = InductorCharacterization(
        name="inductor",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.COMPLETED
    assert result.data["component"] == "inductor"
    assert result.data["point_count"] == 6
    assert result.data["simulated"] is True
    assert result.data["sweep"]["settle_periods"] == 0
    assert result.data["summary"]["inductive_point_count"] == 6
    assert result.data["summary"]["inductance_h_median"] == pytest.approx(
        44.4e-3,
        rel=0.03,
    )
    assert result.data["summary"]["nominal_error_percent"] == pytest.approx(0.0, abs=3.0)
    assert all(point["is_inductive"] for point in result.data["points"])


def test_inductor_validation_rejects_reversed_frequency_range() -> None:
    """The experiment fails fast when stop_hz is not greater than start_hz."""
    config = InductorCharacterizationConfig(
        start_hz=10_000.0,
        stop_hz=100.0,
        num_points=6,
    )
    result = InductorCharacterization(
        name="inductor",
        config=config,
        simulate=True,
    ).run()

    assert result.state == ExperimentState.FAILED
    assert isinstance(result.error, ValueError)


def test_inductor_experiment_is_exported_from_experiments_package() -> None:
    """The public experiments namespace exposes InductorCharacterization."""
    assert ExportedInductorCharacterization is InductorCharacterization
