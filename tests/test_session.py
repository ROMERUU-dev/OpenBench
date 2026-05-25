"""Tests for measurement session reproducibility support."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from openbench.core.experiment import ExperimentResult, ExperimentState
from openbench.core.session import MeasurementSession, SessionManager, SessionState


@dataclass(frozen=True)
class DummyConfig:
    """Small dataclass config used to verify reproducible snapshots."""

    start_v: float
    stop_v: float
    points: int


class FakeInstrument:
    """Instrument-like object with the public fields sessions inspect."""

    name = "fake-scope"
    resource = "SIM::SCOPE"
    simulate = True

    def status(self) -> str:
        """Return a stable fake adapter status."""

        return "simulated"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_legacy_metadata_container_usage_still_works() -> None:
    """A direct MeasurementSession(name=...) still supports metadata only."""

    session = MeasurementSession(name="legacy session")

    session.add_metadata("operator", "Romero")

    assert session.metadata == {"operator": "Romero"}
    assert session.path is None
    assert session.state == SessionState.CREATED


def test_session_manager_persists_manifest_metadata_and_result(tmp_path: Path) -> None:
    """Started sessions persist metadata, config, results, and artifact hashes."""

    manager = SessionManager(
        root_dir=tmp_path,
        default_metadata={"operator": "lab"},
    )
    session = manager.create_session(
        "Monday Lab",
        metadata={"sample": "tc4069"},
        config=DummyConfig(start_v=0.0, stop_v=5.0, points=51),
        simulate=True,
        tags=["deadline"],
        include_environment=False,
    )
    result = ExperimentResult(
        name="gain-check",
        data={"gain_v_per_v": 2.0},
        metadata={"fixture": "simulated"},
        state=ExperimentState.COMPLETED,
        duration_s=1.25,
    )

    result_path = session.record_result(result, tag="gain")
    manifest_path = session.close()

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    result_payload = json.loads(result_path.read_text(encoding="utf-8"))

    assert payload["state"] == "completed"
    assert payload["simulate"] is True
    assert payload["metadata"] == {"operator": "lab", "sample": "tc4069"}
    assert payload["config"] == {"start_v": 0.0, "stop_v": 5.0, "points": 51}
    assert payload["tags"] == ["deadline"]
    assert payload["artifacts"][0]["kind"] == "experiment_result"
    assert payload["artifacts"][0]["sha256"] == _sha256(result_path)
    assert result_payload["experiment"] == "gain-check"
    assert result_payload["state"] == "completed"
    assert result_payload["metadata"] == {"fixture": "simulated"}


def test_session_records_instruments_artifacts_and_can_reload(tmp_path: Path) -> None:
    """Instrument snapshots and custom artifacts survive manifest reload."""

    session = MeasurementSession(name="instrument session", root_dir=tmp_path)
    session.start(include_environment=False)

    instrument_record = session.record_instrument(FakeInstrument())
    artifact_path = session.write_json_artifact(
        "raw/notes",
        {"point_count": 3},
        kind="raw_data",
        metadata={"source": "test"},
    )
    session.close()

    loaded = MeasurementSession.from_manifest(session.path or tmp_path)

    assert instrument_record["name"] == "fake-scope"
    assert loaded.session_id == session.session_id
    assert loaded.instruments[0]["status"] == "simulated"
    assert loaded.artifacts[-1].kind == "raw_data"
    assert loaded.artifacts[-1].path == "artifacts/raw/notes.json"
    assert loaded.artifacts[-1].sha256 == _sha256(artifact_path)


def test_json_artifact_path_cannot_escape_session(tmp_path: Path) -> None:
    """Artifact writers reject paths outside the session directory."""

    session = MeasurementSession(name="escape check", root_dir=tmp_path)
    session.start(include_environment=False)

    with pytest.raises(ValueError):
        session.write_json_artifact("../outside", {"unsafe": True})
