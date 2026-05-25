"""Tests for FilterDesignExperiment — simulation mode only (no hardware)."""

from __future__ import annotations

import pytest

from openbench.core.experiment import ExperimentState
from openbench.experiments.filter_design_experiment import (
    FilterDesignExperiment,
    FilterDesignExperimentConfig,
)
from openbench.filters import (
    Approximation,
    DesignInputs,
    FilterKind,
    FilterSpec,
    Topology,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lp_config(
    passband_hz: float = 1_000.0,
    stopband_hz: float = 5_000.0,
    num_sweep_points: int = 20,
) -> FilterDesignExperimentConfig:
    inputs = DesignInputs(
        kind=FilterKind.LOWPASS,
        approximation=Approximation.BUTTERWORTH,
        spec=FilterSpec(passband_hz=passband_hz, stopband_hz=stopband_hz),
        passband_ripple_db=1.0,
        stopband_attenuation_db=40.0,
        topology=Topology.SALLEN_KEY,
    )
    return FilterDesignExperimentConfig(
        design_inputs=inputs,
        num_sweep_points=num_sweep_points,
        reference_ohm=50.0,
        excitation_v=0.1,
        tolerance_db=5.0,
    )


def _make_exp(config: FilterDesignExperimentConfig | None = None) -> FilterDesignExperiment:
    return FilterDesignExperiment(
        name="test-filter-lp",
        config=config or _lp_config(),
        simulate=True,
    )


# ---------------------------------------------------------------------------
# validate() tests
# ---------------------------------------------------------------------------


class TestFilterDesignExperimentValidate:
    def test_valid_config_passes(self) -> None:
        exp = _make_exp()
        exp._effective_simulate = True
        exp.validate()  # must not raise

    def test_too_few_sweep_points_raises(self) -> None:
        inputs = _lp_config().design_inputs
        bad_config = FilterDesignExperimentConfig(
            design_inputs=inputs, num_sweep_points=1
        )
        exp = FilterDesignExperiment(name="x", config=bad_config, simulate=True)
        exp._effective_simulate = True
        with pytest.raises(ValueError, match="num_sweep_points must be >= 2"):
            exp.validate()

    def test_negative_reference_ohm_raises(self) -> None:
        inputs = _lp_config().design_inputs
        bad_config = FilterDesignExperimentConfig(
            design_inputs=inputs, reference_ohm=-1.0
        )
        exp = FilterDesignExperiment(name="x", config=bad_config, simulate=True)
        exp._effective_simulate = True
        with pytest.raises(ValueError, match="reference_ohm must be positive"):
            exp.validate()

    def test_zero_excitation_raises(self) -> None:
        inputs = _lp_config().design_inputs
        bad_config = FilterDesignExperimentConfig(
            design_inputs=inputs, excitation_v=0.0
        )
        exp = FilterDesignExperiment(name="x", config=bad_config, simulate=True)
        exp._effective_simulate = True
        with pytest.raises(ValueError, match="excitation_v must be positive"):
            exp.validate()

    def test_negative_tolerance_raises(self) -> None:
        inputs = _lp_config().design_inputs
        bad_config = FilterDesignExperimentConfig(
            design_inputs=inputs, tolerance_db=-0.1
        )
        exp = FilterDesignExperiment(name="x", config=bad_config, simulate=True)
        exp._effective_simulate = True
        with pytest.raises(ValueError, match="tolerance_db must be positive"):
            exp.validate()


# ---------------------------------------------------------------------------
# Full simulation run tests
# ---------------------------------------------------------------------------


class TestFilterDesignExperimentSimulation:
    def test_run_completes_successfully(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        assert result.state == ExperimentState.COMPLETED
        assert result.error is None

    def test_result_data_has_required_keys(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        data = result.data
        for key in (
            "filter_design",
            "measurement_setup",
            "frequencies_hz",
            "theoretical_magnitude_db",
            "theoretical_phase_deg",
            "validation",
            "simulated",
        ):
            assert key in data, f"Missing key in result data: {key!r}"

    def test_simulated_flag_is_true(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        assert result.data["simulated"] is True

    def test_filter_design_order_populated(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        assert result.data["filter_design"]["order"] >= 2

    def test_sweep_length_matches_num_points(self) -> None:
        cfg = _lp_config(num_sweep_points=30)
        exp = _make_exp(cfg)
        result = exp.run(simulate=True)
        assert len(result.data["frequencies_hz"]) == 30
        assert len(result.data["theoretical_magnitude_db"]) == 30

    def test_simulation_passes_tolerance(self) -> None:
        """Simulated backend mirrors theory, so RMS error should be ~0 dB."""
        exp = _make_exp()
        result = exp.run(simulate=True)
        val = result.data["validation"]
        assert val["passed"] is True
        rms = val["rms_magnitude_error_db"]
        assert rms is not None
        assert abs(rms) < 0.01, f"Expected near-zero RMS error in simulation, got {rms}"

    def test_measured_data_present_in_simulation(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        assert result.data["measured_magnitude_db"] is not None
        assert result.data["measured_phase_deg"] is not None

    def test_measurement_setup_contains_notes(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        notes = result.data["measurement_setup"]["notes"]
        assert isinstance(notes, list)
        assert len(notes) > 0

    def test_highpass_filter_runs_in_simulation(self) -> None:
        inputs = DesignInputs(
            kind=FilterKind.HIGHPASS,
            approximation=Approximation.BUTTERWORTH,
            spec=FilterSpec(passband_hz=2_000.0, stopband_hz=500.0),
            passband_ripple_db=1.0,
            stopband_attenuation_db=40.0,
            topology=Topology.SALLEN_KEY,
        )
        config = FilterDesignExperimentConfig(
            design_inputs=inputs,
            num_sweep_points=15,
            tolerance_db=5.0,
        )
        exp = FilterDesignExperiment(
            name="test-filter-hp", config=config, simulate=True
        )
        result = exp.run(simulate=True)
        assert result.state == ExperimentState.COMPLETED
        assert result.data["filter_design"]["kind"] == str(FilterKind.HIGHPASS)

    def test_experiment_state_after_completed_run(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        assert result.state == ExperimentState.COMPLETED
        assert exp.state() == ExperimentState.COMPLETED

    def test_duration_is_positive(self) -> None:
        exp = _make_exp()
        result = exp.run(simulate=True)
        assert result.duration_s >= 0.0
