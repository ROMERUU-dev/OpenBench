"""End-to-end simulation physics-validation tests for the Chua lab workflow.

These tests go beyond "the experiment completed" and verify that simulated
data is physically plausible: thresholds in expected ranges, inductance
within tolerance of nominal, and Chua-diode conductances correctly negative
in both piecewise-linear segments.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

from openbench.core.experiment import ExperimentState
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


def _load_workflow_module():
    spec = importlib.util.spec_from_file_location(
        "chua_lab_workflow",
        Path(__file__).parent.parent / "examples" / "chua_lab_workflow.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# TC4069UBP inverter physics
# ---------------------------------------------------------------------------


class TestTC4069UBPPhysics:
    """Verify the simulated TC4069UBP transfer curve is physically reasonable."""

    @pytest.fixture(scope="class")
    def result(self):
        """Run the TC4069UBP experiment once and share the result."""
        exp = TC4069UBPCharacterization(
            name="tc4069-e2e",
            config=TC4069UBPCharacterizationConfig(
                supply_voltage_v=5.0,
                input_start_v=0.0,
                input_stop_v=5.0,
                input_step_v=0.1,
            ),
            simulate=True,
        )
        return exp.run()

    def test_experiment_completed(self, result) -> None:
        """Experiment must reach COMPLETED state without error."""
        assert result.state == ExperimentState.COMPLETED
        assert result.error is None

    def test_switching_threshold_near_vdd_half(self, result) -> None:
        """Switching threshold must lie within ±35% of VDD/2.

        The sigmoidal model is centred at 0.48 × VDD ≈ 2.4 V, which puts the
        50% crossing very close to VDD/2 = 2.5 V.
        """
        threshold = result.data.get("switching_threshold_v")
        assert threshold is not None, "switching_threshold_v must be present in data"
        vdd = result.data["supply_voltage_v"]
        half_vdd = vdd / 2.0
        error_frac = abs(threshold - half_vdd) / half_vdd
        assert error_frac < 0.35, (
            f"Threshold {threshold:.3f} V deviates {error_frac:.1%} from "
            f"VDD/2 = {half_vdd:.3f} V"
        )

    def test_output_decreases_with_input(self, result) -> None:
        """Inverter output must decrease monotonically on average.

        The mean output in the first quarter of the sweep (low VIN) must
        exceed the mean output in the last quarter (high VIN).
        """
        points = result.data["points"]
        n = len(points)
        q = max(n // 4, 1)
        low_mean = sum(p["output_voltage_v"] for p in points[:q]) / q
        high_mean = sum(p["output_voltage_v"] for p in points[-q:]) / q
        assert low_mean > high_mean, (
            f"Low-VIN mean output ({low_mean:.3f} V) should exceed "
            f"high-VIN mean output ({high_mean:.3f} V)"
        )

    def test_output_bounded_by_supply(self, result) -> None:
        """Output voltage must stay within [0, VDD] at every sweep point."""
        vdd = result.data["supply_voltage_v"]
        for pt in result.data["points"]:
            vout = pt["output_voltage_v"]
            assert 0.0 <= vout <= vdd, (
                f"Output {vout:.3f} V is outside [0, {vdd:.1f} V] at "
                f"VIN={pt['input_voltage_v']:.3f} V"
            )

    def test_supply_current_positive(self, result) -> None:
        """Simulated supply current must be positive at every point."""
        for pt in result.data["points"]:
            ia = pt.get("supply_current_a")
            if ia is not None:
                assert ia >= 0.0, (
                    f"Negative supply current {ia*1e3:.3f} mA at "
                    f"VIN={pt['input_voltage_v']:.3f} V"
                )


# ---------------------------------------------------------------------------
# Inductor characterization physics
# ---------------------------------------------------------------------------


class TestInductorCharacterizationPhysics:
    """Verify the simulated SR860 inductor sweep produces realistic values."""

    _NOMINAL_L_H = 44.4e-3

    @pytest.fixture(scope="class")
    def result(self):
        """Run the inductor experiment once and share the result."""
        config = InductorCharacterizationConfig(
            start_hz=100.0,
            stop_hz=100_000.0,
            num_points=30,
            excitation_v=1.0,
            log_scale=True,
            time_constant_s=0.1,
            series_resistor_ohm=220.0,
            source_series_ohm=50.0,
            nominal_inductance_h=self._NOMINAL_L_H,
            simulation_inductance_h=self._NOMINAL_L_H,
            simulation_series_resistance_ohm=10.0,
        )
        exp = InductorCharacterization(
            name="inductor-e2e",
            config=config,
            simulate=True,
        )
        return exp.run()

    def test_experiment_completed(self, result) -> None:
        """Experiment must reach COMPLETED state without error."""
        assert result.state == ExperimentState.COMPLETED
        assert result.error is None

    def test_median_inductance_within_20pct_of_nominal(self, result) -> None:
        """Median inductance must be within ±20% of the configured 44.4 mH."""
        summary = result.data["summary"]
        l_median_h = summary.get("inductance_h_median")
        assert l_median_h is not None, "inductance_h_median must be in summary"
        assert l_median_h > 0.0, "Median inductance must be positive"
        error_pct = abs(l_median_h - self._NOMINAL_L_H) / self._NOMINAL_L_H * 100.0
        assert error_pct < 20.0, (
            f"Median L = {l_median_h*1e3:.2f} mH deviates {error_pct:.1f}% "
            f"from nominal {self._NOMINAL_L_H*1e3:.1f} mH"
        )

    def test_inductance_positive_below_5khz(self, result) -> None:
        """Inductance must be positive well below the self-resonance frequency."""
        points = result.data["points"]
        low_freq = [p for p in points if p["frequency_hz"] < 5_000.0]
        assert low_freq, "Expected at least one point below 5 kHz"
        for pt in low_freq:
            assert pt["inductance_h"] > 0.0, (
                f"Negative inductance {pt['inductance_h']*1e3:.2f} mH "
                f"at {pt['frequency_hz']:.0f} Hz (below SRF)"
            )

    def test_impedance_increases_with_frequency(self, result) -> None:
        """Impedance magnitude must be higher at high frequencies than at low.

        For an inductive DUT, |Z| ≈ sqrt(R² + (ωL)²) grows with ω, so
        the mean |Z| of the top 5 frequency points must exceed the bottom 5.
        """
        points = sorted(result.data["points"], key=lambda p: p["frequency_hz"])
        low_z = [p["magnitude_ohm"] for p in points[:5]]
        high_z = [p["magnitude_ohm"] for p in points[-5:]]
        low_mean = sum(low_z) / len(low_z)
        high_mean = sum(high_z) / len(high_z)
        assert high_mean > low_mean, (
            f"|Z| at high frequencies ({high_mean:.1f} Ω) should exceed "
            f"|Z| at low frequencies ({low_mean:.1f} Ω)"
        )

    def test_nominal_error_reported_in_summary(self, result) -> None:
        """nominal_error_percent must be present and finite when nominal L is set."""
        summary = result.data["summary"]
        assert "nominal_error_percent" in summary, (
            "nominal_error_percent must appear in summary when nominal_inductance_h is set"
        )
        assert math.isfinite(summary["nominal_error_percent"]), (
            "nominal_error_percent must be a finite number"
        )

    def test_phase_positive_inductive(self, result) -> None:
        """Phase angle should be positive (inductive) at low frequencies."""
        points = sorted(result.data["points"], key=lambda p: p["frequency_hz"])
        low_freq = [p for p in points[:10] if p["frequency_hz"] < 10_000.0]
        assert low_freq, "Expected low-frequency points"
        for pt in low_freq:
            assert pt["phase_deg"] > 0.0, (
                f"Phase {pt['phase_deg']:.1f}° should be positive (inductive) "
                f"at {pt['frequency_hz']:.0f} Hz"
            )


# ---------------------------------------------------------------------------
# Chua diode admittance physics
# ---------------------------------------------------------------------------


class TestChuaAdmittancePhysics:
    """Verify the simulated Chua diode admittance sweep matches the PWL model."""

    _GA_S = -0.757e-3   # inner segment conductance (siemens)
    _GB_S = -0.409e-3   # outer segment conductance (siemens)
    _BP_V = 1.0          # breakpoint voltage (volts)

    @pytest.fixture(scope="class")
    def result(self):
        """Run the Chua admittance experiment once and share the result."""
        config = ChuaAdmittanceSweepConfig(
            bias_start_v=-2.0,
            bias_stop_v=2.0,
            bias_step_v=0.1,
            ac_frequency_hz=1_000.0,
            excitation_v=0.1,
            settle_s=0.0,
            time_constant_s=0.1,
            series_resistor_ohm=220.0,
            source_series_ohm=50.0,
            simulation_ga_s=self._GA_S,
            simulation_gb_s=self._GB_S,
            simulation_bp_v=self._BP_V,
            simulation_cpar_f=10e-9,
        )
        exp = ChuaAdmittanceSweep(
            name="chua-admittance-e2e",
            config=config,
            simulate=True,
        )
        return exp.run()

    def test_experiment_completed(self, result) -> None:
        """Experiment must reach COMPLETED state without error."""
        assert result.state == ExperimentState.COMPLETED
        assert result.error is None

    def test_all_conductances_negative(self, result) -> None:
        """Every measurement point must have negative conductance.

        The classic Chua element has negative resistance throughout — both
        inner and outer segments have Ga < 0 and Gb < 0.
        """
        for pt in result.data["points"]:
            assert pt["conductance_s"] < 0.0, (
                f"Positive conductance {pt['conductance_s']*1e3:.4f} mS "
                f"at V = {pt['bias_voltage_v']:.2f} V — Chua element is "
                "always a negative-resistance device"
            )

    def test_inner_region_more_negative_than_outer(self, result) -> None:
        """Inner-segment conductance (|V| < Bp) must be more negative than outer.

        |Ga| > |Gb| by definition of the classic Chua diode parameters, so the
        mean conductance of inner-region points should be more negative than that
        of outer-region points.
        """
        inner_g = [
            pt["conductance_s"]
            for pt in result.data["points"]
            if abs(pt["bias_voltage_v"]) < self._BP_V - 0.05
        ]
        outer_g = [
            pt["conductance_s"]
            for pt in result.data["points"]
            if abs(pt["bias_voltage_v"]) > self._BP_V + 0.05
        ]
        assert inner_g and outer_g, "Expected measurements in both segments"
        inner_mean = sum(inner_g) / len(inner_g)
        outer_mean = sum(outer_g) / len(outer_g)
        assert inner_mean < outer_mean, (
            f"Inner mean G = {inner_mean*1e3:.4f} mS should be more negative than "
            f"outer mean G = {outer_mean*1e3:.4f} mS"
        )

    def test_conductance_extremes_near_model_parameters(self, result) -> None:
        """G_min and G_max in the summary must be close to Ga and Gb.

        Small noise is added per point, so a 10% tolerance on each is sufficient.
        """
        summary = result.data["summary"]
        g_min = summary["conductance_s_min"]
        g_max = summary["conductance_s_max"]

        ga_err = abs(g_min - self._GA_S) / abs(self._GA_S)
        gb_err = abs(g_max - self._GB_S) / abs(self._GB_S)

        assert ga_err < 0.10, (
            f"G_min = {g_min*1e3:.4f} mS deviates {ga_err:.1%} from "
            f"Ga = {self._GA_S*1e3:.3f} mS"
        )
        assert gb_err < 0.10, (
            f"G_max = {g_max*1e3:.4f} mS deviates {gb_err:.1%} from "
            f"Gb = {self._GB_S*1e3:.3f} mS"
        )

    def test_all_points_negative_resistance_counted(self, result) -> None:
        """Summary negative_resistance_point_count must equal total point_count."""
        data = result.data
        assert data["summary"]["negative_resistance_point_count"] == data["point_count"], (
            "Every point in the classic Chua admittance sweep should be in the "
            "negative-resistance region — negative_resistance_point_count must equal point_count"
        )

    def test_admittance_magnitude_positive(self, result) -> None:
        """Admittance magnitude |Y| must be positive at every point."""
        for pt in result.data["points"]:
            assert pt["admittance_s"] > 0.0, (
                f"|Y| = {pt['admittance_s']*1e3:.4f} mS must be positive at "
                f"V = {pt['bias_voltage_v']:.2f} V"
            )

    def test_symmetric_point_count_around_zero(self, result) -> None:
        """Sweep from -2 V to +2 V with step 0.1 V must produce ≥ 39 points."""
        assert result.data["point_count"] >= 39, (
            "Sweep from -2 V to +2 V step 0.1 V should yield ≥ 39 points"
        )


# ---------------------------------------------------------------------------
# Full workflow integration (end-to-end)
# ---------------------------------------------------------------------------


class TestFullWorkflowE2E:
    """Integration tests that run all three experiments through the CLI helper."""

    @pytest.fixture(scope="class")
    def workflow_results(self, tmp_path_factory) -> dict:
        """Run the complete workflow once and return all three results."""
        out = tmp_path_factory.mktemp("chua_e2e")
        mod = _load_workflow_module()
        return mod.run_workflow(simulate=True, only=None, output_dir=out)

    def test_all_three_experiments_present(self, workflow_results) -> None:
        """Workflow must return results for all three experiment keys."""
        assert set(workflow_results.keys()) == {"tc4069", "inductor", "chua"}

    def test_all_experiments_completed(self, workflow_results) -> None:
        """Every experiment must reach COMPLETED state."""
        for key, result in workflow_results.items():
            assert result.state == ExperimentState.COMPLETED, (
                f"Experiment '{key}' ended in state '{result.state}': {result.error}"
            )

    def test_all_durations_non_negative(self, workflow_results) -> None:
        """Measured wall-clock duration must be ≥ 0 for every experiment."""
        for key, result in workflow_results.items():
            assert result.duration_s >= 0.0, (
                f"Experiment '{key}' has negative duration: {result.duration_s}"
            )

    def test_simulated_flag_set_in_data(self, workflow_results) -> None:
        """Each result's data dict must record simulated=True."""
        for key, result in workflow_results.items():
            assert result.data.get("simulated") is True, (
                f"Experiment '{key}' data must contain simulated=True"
            )

    def test_json_files_saved_with_completed_state(self, tmp_path: Path) -> None:
        """Each of the three experiments must produce a completed JSON file."""
        mod = _load_workflow_module()
        mod.run_workflow(simulate=True, only=None, output_dir=tmp_path)
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 3, (
            f"Expected 3 JSON result files, found: {[f.name for f in json_files]}"
        )
        for path in json_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["state"] == "completed", (
                f"{path.name}: state should be 'completed', got {payload['state']!r}"
            )
            assert payload["error"] is None, (
                f"{path.name}: error should be null, got {payload['error']!r}"
            )
            assert payload["data"].get("point_count", 0) > 0, (
                f"{path.name}: point_count must be > 0"
            )

    def test_cli_simulate_returns_zero(self, tmp_path: Path, monkeypatch) -> None:
        """main() must return exit code 0 when all experiments succeed."""
        monkeypatch.setattr(
            "sys.argv",
            ["chua_lab_workflow.py", "--simulate", "--output-dir", str(tmp_path)],
        )
        mod = _load_workflow_module()
        assert mod.main() == 0

    def test_cli_verbose_flag_does_not_crash(self, tmp_path: Path, monkeypatch) -> None:
        """main() with --verbose must succeed and return exit code 0."""
        monkeypatch.setattr(
            "sys.argv",
            [
                "chua_lab_workflow.py",
                "--simulate",
                "--verbose",
                "--output-dir",
                str(tmp_path),
            ],
        )
        mod = _load_workflow_module()
        assert mod.main() == 0

    def test_subset_only_inductor(self, tmp_path: Path) -> None:
        """Running only=["inductor"] must return exactly one result and one JSON."""
        mod = _load_workflow_module()
        results = mod.run_workflow(simulate=True, only=["inductor"], output_dir=tmp_path)
        assert list(results.keys()) == ["inductor"]
        assert results["inductor"].state == ExperimentState.COMPLETED
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 1
