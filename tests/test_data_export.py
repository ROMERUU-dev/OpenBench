"""Tests for HDF5 data export."""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from openbench.core.session import MeasurementSession
from openbench.data import DataRecorder
from openbench.utils.data_export import HDF5_SCHEMA_VERSION, export_hdf5


def _read_text_scalar(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def test_export_hdf5_writes_columnar_data_and_metadata(tmp_path: Path) -> None:
    """Column-oriented numeric data is written as compressed HDF5 datasets."""

    output_path = tmp_path / "frequency_sweep.h5"
    record = export_hdf5(
        output_path,
        "Frequency Sweep",
        {
            "frequency_hz": np.linspace(100.0, 1_000.0, 8),
            "impedance_ohm": np.linspace(1_000.0, 2_000.0, 8),
            "instrument": "sr860-sim",
            "instrument_metadata": {"mode": "simulation"},
        },
        metadata={"operator": "lab", "phase": "6-data"},
    )

    assert record.hdf5_path == output_path
    assert record.row_count == 8
    assert record.fields == (
        "frequency_hz",
        "impedance_ohm",
        "instrument",
        "instrument_metadata.mode",
    )
    assert len(record.sha256) == 64

    with h5py.File(output_path, "r") as h5file:
        assert h5file.attrs["schema_version"] == HDF5_SCHEMA_VERSION
        assert h5file.attrs["format"] == "hdf5"
        assert h5file.attrs["row_count"] == 8
        assert json.loads(h5file.attrs["metadata_json"]) == {
            "operator": "lab",
            "phase": "6-data",
        }
        assert np.allclose(h5file["data/frequency_hz"][:], np.linspace(100.0, 1_000.0, 8))
        assert h5file["data/frequency_hz"].compression == "gzip"
        assert h5file["data/frequency_hz"].chunks is not None
        assert _read_text_scalar(h5file["data/instrument"][()]) == "sr860-sim"
        assert (
            _read_text_scalar(h5file["data/instrument_metadata.mode"][()])
            == "simulation"
        )


def test_data_recorder_hdf5_registers_session_artifact(tmp_path: Path) -> None:
    """Session-backed HDF5 recording registers a measurement artifact."""

    session = MeasurementSession(name="hdf5 session", root_dir=tmp_path / "sessions")
    session.start(include_environment=False)
    recorder = DataRecorder(session=session, default_metadata={"operator": "lab"})

    record = recorder.record_hdf5(
        "Large Waveform",
        {
            "time_s": np.arange(4, dtype=float) * 1e-6,
            "voltage_v": np.array([0.1, 0.2, 0.15, 0.05], dtype=float),
        },
        metadata={"backend": "scope-sim"},
    )

    manifest = json.loads((session.path / "manifest.json").read_text(encoding="utf-8"))
    artifact_kinds = {artifact["kind"] for artifact in manifest["artifacts"]}
    artifact_paths = {artifact["path"] for artifact in manifest["artifacts"]}
    event_names = [event["name"] for event in manifest["events"]]

    assert record.hdf5_path.parent == session.path / "artifacts" / "data"
    assert artifact_kinds == {"measurement_hdf5"}
    assert f"artifacts/data/{record.hdf5_path.name}" in artifact_paths
    assert "hdf5_data_recorded" in event_names

    with h5py.File(record.hdf5_path, "r") as h5file:
        assert json.loads(h5file.attrs["metadata_json"]) == {
            "backend": "scope-sim",
            "operator": "lab",
        }
        assert np.allclose(h5file["data/voltage_v"][:], [0.1, 0.2, 0.15, 0.05])
