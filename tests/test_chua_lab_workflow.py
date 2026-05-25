"""Tests for the Chua Monday lab workflow script."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openbench.core.experiment import ExperimentState


def _import_workflow():
    """Import chua_lab_workflow from the examples directory."""
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "chua_lab_workflow",
        pathlib.Path(__file__).parent.parent / "examples" / "chua_lab_workflow.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def wf():
    return _import_workflow()


def test_workflow_imports_successfully(wf) -> None:
    """The workflow module must be importable without error."""
    assert hasattr(wf, "run_workflow")
    assert hasattr(wf, "main")


def test_tc4069_simulation_completes(wf, tmp_path: Path) -> None:
    """TC4069UBP characterization completes successfully in simulation mode."""
    result = wf.run_tc4069_characterization(simulate=True, output_dir=tmp_path)
    assert result.state == ExperimentState.COMPLETED
    assert result.data.get("point_count", 0) > 0


def test_inductor_simulation_completes(wf, tmp_path: Path) -> None:
    """Inductor characterization completes successfully in simulation mode."""
    result = wf.run_inductor_characterization(simulate=True, output_dir=tmp_path)
    assert result.state == ExperimentState.COMPLETED
    assert result.data.get("point_count", 0) > 0


def test_chua_admittance_simulation_completes(wf, tmp_path: Path) -> None:
    """Chua admittance sweep completes successfully in simulation mode."""
    result = wf.run_chua_admittance_sweep(simulate=True, output_dir=tmp_path)
    assert result.state == ExperimentState.COMPLETED
    assert result.data.get("point_count", 0) > 0


def test_full_workflow_simulation(wf, tmp_path: Path) -> None:
    """Full workflow runs all three experiments in simulation mode."""
    results = wf.run_workflow(simulate=True, only=None, output_dir=tmp_path)
    assert set(results.keys()) == {"tc4069", "inductor", "chua"}
    for key, result in results.items():
        assert result.state == ExperimentState.COMPLETED, (
            f"Experiment {key!r} failed: {result.error}"
        )


def test_workflow_only_subset(wf, tmp_path: Path) -> None:
    """The --only filter runs a subset and omits the rest."""
    results = wf.run_workflow(simulate=True, only=["tc4069", "chua"], output_dir=tmp_path)
    assert "tc4069" in results
    assert "chua" in results
    assert "inductor" not in results


def test_results_saved_as_json(wf, tmp_path: Path) -> None:
    """Each experiment result is persisted as a JSON file in output_dir."""
    wf.run_workflow(simulate=True, only=None, output_dir=tmp_path)
    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 3, f"Expected 3 JSON files, got {[f.name for f in json_files]}"
    for path in json_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "experiment" in payload
        assert "state" in payload
        assert "data" in payload


def test_cli_simulate_flag(wf, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 0 (success) when all experiments complete in simulation."""
    monkeypatch.setattr(
        "sys.argv",
        ["chua_lab_workflow.py", "--simulate", "--output-dir", str(tmp_path)],
    )
    exit_code = wf.main()
    assert exit_code == 0
