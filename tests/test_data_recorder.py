"""Tests for CSV data recording with JSON metadata."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from openbench.core.interfaces import OscilloscopeReading
from openbench.core.session import MeasurementSession
from openbench.data import DataRecorder
from openbench.data.recorder import RECORDER_SCHEMA_VERSION


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_data_recorder_writes_csv_and_metadata(tmp_path: Path) -> None:
    """Row data is written as CSV with merged JSON sidecar metadata."""

    recorder = DataRecorder(
        tmp_path,
        default_metadata={"operator": "lab"},
    )

    record = recorder.record(
        "DC Sweep",
        [
            {"voltage_v": 0.0, "current_a": 0.001},
            {"voltage_v": 1.0, "current_a": 0.002},
        ],
        metadata={"instrument": "sim-dc"},
    )

    rows = _read_csv(record.csv_path)
    metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))

    assert record.row_count == 2
    assert record.fields == ("voltage_v", "current_a")
    assert rows == [
        {"voltage_v": "0.0", "current_a": "0.001"},
        {"voltage_v": "1.0", "current_a": "0.002"},
    ]
    assert metadata["schema_version"] == RECORDER_SCHEMA_VERSION
    assert metadata["row_count"] == 2
    assert metadata["fields"] == ["voltage_v", "current_a"]
    assert metadata["metadata"] == {"operator": "lab", "instrument": "sim-dc"}
    assert len(metadata["csv_sha256"]) == 64


def test_data_recorder_expands_oscilloscope_reading(tmp_path: Path) -> None:
    """Dataclass readings with parallel vectors become sample rows."""

    reading = OscilloscopeReading(
        channel=1,
        time_s=[0.0, 1e-6],
        voltage_v=[0.2, 0.4],
        sample_rate_hz=1_000_000.0,
        metadata={"backend": "scope-sim", "points": 2},
    )
    recorder = DataRecorder(tmp_path)

    record = recorder.record_measurement("Scope CH1", reading)

    rows = _read_csv(record.csv_path)
    assert record.row_count == 2
    assert rows[0]["channel"] == "1"
    assert rows[0]["time_s"] == "0.0"
    assert rows[1]["time_s"] == "1e-06"
    assert rows[1]["voltage_v"] == "0.4"
    assert rows[0]["sample_rate_hz"] == "1000000.0"
    assert rows[0]["metadata.backend"] == "scope-sim"
    assert rows[0]["metadata.points"] == "2"


def test_data_recorder_registers_session_artifacts(tmp_path: Path) -> None:
    """A session-backed recorder registers CSV and metadata artifacts."""

    session = MeasurementSession(name="data session", root_dir=tmp_path / "sessions")
    session.start(include_environment=False)
    recorder = DataRecorder(session=session)

    record = recorder.record("points", {"x": [0, 1], "y": [2.0, 3.0]})

    manifest = json.loads((session.path / "manifest.json").read_text(encoding="utf-8"))
    artifact_kinds = {artifact["kind"] for artifact in manifest["artifacts"]}
    artifact_paths = {artifact["path"] for artifact in manifest["artifacts"]}
    event_names = [event["name"] for event in manifest["events"]]

    assert record.csv_path.parent == session.path / "artifacts" / "data"
    assert artifact_kinds == {"measurement_csv", "measurement_metadata"}
    assert f"artifacts/data/{record.csv_path.name}" in artifact_paths
    assert f"artifacts/data/{record.metadata_path.name}" in artifact_paths
    assert "data_recorded" in event_names
