"""Tests for experiment-aware OpenBench plotting templates."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from openbench.data import DataRecorder, ExperimentPlotter, PlotTemplate


def test_plotter_saves_chua_template_png(tmp_path: Path) -> None:
    """Chua admittance payloads are auto-detected and saved as plot artifacts."""

    payload = {
        "experiment": "chua_admittance_sweep",
        "points": [
            {"bias_voltage_v": -1.0, "conductance_s": -0.0007, "susceptance_s": 0.00001},
            {"bias_voltage_v": 0.0, "conductance_s": -0.00075, "susceptance_s": 0.00002},
            {"bias_voltage_v": 1.0, "conductance_s": -0.0004, "susceptance_s": 0.00001},
        ],
    }
    plotter = ExperimentPlotter()

    artifact = plotter.save(payload, tmp_path / "chua.png")

    assert artifact.path.exists()
    assert artifact.template == "chua_admittance_sweep"
    assert artifact.format == "png"
    assert artifact.path.stat().st_size > 0


def test_plotter_handles_filter_design_column_payload() -> None:
    """Filter validation templates handle top-level column-oriented arrays."""

    payload = {
        "filter_design": {"order": 2, "kind": "lowpass"},
        "validation": {"passed": True},
        "frequencies_hz": [100.0, 1_000.0, 10_000.0],
        "theoretical_magnitude_db": [0.0, -3.0, -40.0],
        "measured_magnitude_db": [0.1, -3.2, -39.0],
        "theoretical_phase_deg": [-1.0, -45.0, -89.0],
        "measured_phase_deg": [-2.0, -44.0, -88.0],
    }
    plotter = ExperimentPlotter()

    figure = plotter.plot(payload)

    assert isinstance(plotter.template_for(payload), PlotTemplate)
    assert figure.axes[0].get_xscale() == "log"
    assert figure.axes[0].get_ylabel() == "Magnitude (dB)"
    assert len(figure.axes[0].lines) == 2
    assert len(figure.axes[1].lines) == 2


def test_plotter_reads_data_record_csv(tmp_path: Path) -> None:
    """CSV records from DataRecorder can be plotted without backend objects."""

    recorder = DataRecorder(tmp_path / "data")
    record = recorder.record(
        "tc4069 transfer",
        [
            {"input_voltage_v": 0.0, "output_voltage_v": 5.0, "supply_current_a": 0.001},
            {"input_voltage_v": 2.5, "output_voltage_v": 2.4, "supply_current_a": 0.002},
            {"input_voltage_v": 5.0, "output_voltage_v": 0.0, "supply_current_a": 0.001},
        ],
        metadata={"experiment_type": "tc4069_transfer"},
    )
    plotter = ExperimentPlotter()

    artifact = plotter.save(record, tmp_path / "plots" / "tc4069.png")

    assert artifact.path.exists()
    assert artifact.template == "tc4069_transfer"
    assert artifact.path.stat().st_size > 0
