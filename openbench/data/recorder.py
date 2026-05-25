"""CSV measurement recording with sidecar JSON metadata."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from openbench.core.experiment import ExperimentResult
from openbench.core.session import MeasurementSession

logger = logging.getLogger(__name__)

RECORDER_SCHEMA_VERSION = 1
DEFAULT_DATA_ROOT = Path("openbench_data")


@dataclass(frozen=True)
class DataRecord:
    """Descriptor for files written by ``DataRecorder``.

    Attributes:
        name: Human-readable dataset name supplied by the caller.
        csv_path: Path to the written CSV file.
        metadata_path: Path to the JSON sidecar metadata file.
        row_count: Number of CSV data rows written.
        fields: Ordered CSV field names.
        created_at: UTC timestamp when the record was created.
        metadata: User and recorder metadata persisted in the sidecar file.
    """

    name: str
    csv_path: Path
    metadata_path: Path
    row_count: int
    fields: tuple[str, ...]
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable record description.

        Returns:
            Dictionary describing the written CSV and metadata files.
        """

        return {
            "name": self.name,
            "csv_path": str(self.csv_path),
            "metadata_path": str(self.metadata_path),
            "row_count": self.row_count,
            "fields": list(self.fields),
            "created_at": _to_jsonable(self.created_at),
            "metadata": _to_jsonable(self.metadata),
        }


class DataRecorder:
    """Write OpenBench measurement data as CSV plus JSON metadata.

    ``DataRecorder`` accepts row-oriented data, column-oriented mappings, and
    dataclass-backed readings from the instrument interfaces. It keeps backend
    adapters independent from persistence while giving experiments a stable
    recorder that can also register outputs in a running
    ``MeasurementSession``.

    Attributes:
        output_dir: Directory where CSV and metadata files are written.
        session: Optional running measurement session that receives artifact
            registrations for every file written.
        default_metadata: Metadata merged into every sidecar JSON file.
    """

    def __init__(
        self,
        output_dir: Path | str | None = None,
        *,
        session: MeasurementSession | None = None,
        default_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize a recorder.

        Args:
            output_dir: Directory for recorder outputs. When omitted and
                ``session`` is started, files are written under
                ``<session>/artifacts/data``. Otherwise ``openbench_data`` is
                used.
            session: Optional started session used to register recorder
                artifacts.
            default_metadata: Metadata merged into every JSON sidecar.

        Raises:
            RuntimeError: If ``session`` is supplied before it has been started.
        """

        if output_dir is None and session is not None:
            if session.path is None or not session.path.exists():
                raise RuntimeError("DataRecorder session must be started.")
            output_dir = session.path / "artifacts" / "data"

        self.output_dir = Path(output_dir or DEFAULT_DATA_ROOT)
        self.session = session
        self.default_metadata = _to_jsonable(default_metadata or {})

    def record(
        self,
        name: str,
        data: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
        fieldnames: Sequence[str] | None = None,
    ) -> DataRecord:
        """Persist measurement data to CSV and JSON metadata files.

        Args:
            name: Human-readable dataset name used in output file names.
            data: Row-oriented iterable, column-oriented mapping, dataclass, or
                dataframe-like object to persist.
            metadata: Extra metadata for the JSON sidecar.
            fieldnames: Optional preferred CSV field order.

        Returns:
            ``DataRecord`` describing the written files.

        Raises:
            ValueError: If ``name`` is empty, data cannot be tabulated, or no
                fields can be inferred for an empty dataset.
        """

        if not name.strip():
            raise ValueError("Record name must not be empty.")

        rows = _rows_from_data(data)
        fields = _field_order(rows, fieldnames)
        if not fields:
            raise ValueError("Cannot write CSV without fields.")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(UTC)
        base_name = self._unique_base_name(name, created_at)
        csv_path = self.output_dir / f"{base_name}.csv"
        metadata_path = self.output_dir / f"{base_name}.metadata.json"

        logger.info("Recording measurement data: %s rows=%d", name, len(rows))
        _write_csv(csv_path, rows, fields)

        merged_metadata: dict[str, Any] = dict(self.default_metadata)
        if metadata is not None:
            merged_metadata.update(_to_jsonable(metadata))

        metadata_payload = {
            "schema_version": RECORDER_SCHEMA_VERSION,
            "name": name,
            "created_at": created_at,
            "format": "csv",
            "csv_file": csv_path.name,
            "csv_sha256": _sha256_file(csv_path),
            "row_count": len(rows),
            "fields": list(fields),
            "metadata": merged_metadata,
        }
        _write_json(metadata_path, metadata_payload)

        record = DataRecord(
            name=name,
            csv_path=csv_path,
            metadata_path=metadata_path,
            row_count=len(rows),
            fields=tuple(fields),
            created_at=created_at,
            metadata=merged_metadata,
        )
        self._register_session_artifacts(record)
        logger.debug("Measurement data recorded: %s", record.to_dict())
        return record

    def record_experiment_result(
        self,
        result: ExperimentResult,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        fieldnames: Sequence[str] | None = None,
    ) -> DataRecord:
        """Persist the data payload from an experiment result.

        Args:
            result: Result returned by ``BaseExperiment.run()``.
            name: Optional output dataset name. Defaults to ``result.name``.
            metadata: Extra sidecar metadata merged after result metadata.
            fieldnames: Optional preferred CSV field order.

        Returns:
            ``DataRecord`` for the CSV and JSON sidecar files.
        """

        merged_metadata: dict[str, Any] = {
            "experiment": result.name,
            "state": result.state,
            "duration_s": result.duration_s,
            "result_metadata": result.metadata,
        }
        if result.error is not None:
            merged_metadata["error"] = result.error
        if metadata is not None:
            merged_metadata.update(_to_jsonable(metadata))

        return self.record(
            name or result.name,
            result.data,
            metadata=merged_metadata,
            fieldnames=fieldnames,
        )

    def record_measurement(
        self,
        name: str,
        measurement: object,
        *,
        metadata: Mapping[str, Any] | None = None,
        fieldnames: Sequence[str] | None = None,
    ) -> DataRecord:
        """Persist one backend measurement object or a list of them.

        Args:
            name: Human-readable dataset name.
            measurement: Instrument reading dataclass, mapping, or iterable of
                readings.
            metadata: Extra sidecar metadata.
            fieldnames: Optional preferred CSV field order.

        Returns:
            ``DataRecord`` for the measurement files.
        """

        return self.record(
            name,
            measurement,
            metadata=metadata,
            fieldnames=fieldnames,
        )

    def _unique_base_name(self, name: str, created_at: datetime) -> str:
        timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        slug = _slugify(name)
        base = f"{timestamp}_{slug}"
        candidate = base
        index = 1
        while (
            (self.output_dir / f"{candidate}.csv").exists()
            or (self.output_dir / f"{candidate}.metadata.json").exists()
        ):
            index += 1
            candidate = f"{base}_{index}"
        return candidate

    def _register_session_artifacts(self, record: DataRecord) -> None:
        if self.session is None:
            return

        artifact_metadata = {
            "record": record.name,
            "row_count": record.row_count,
            "fields": list(record.fields),
        }
        self.session.register_artifact(
            record.csv_path,
            kind="measurement_csv",
            name=record.csv_path.name,
            metadata={**artifact_metadata, "metadata_file": record.metadata_path.name},
        )
        self.session.register_artifact(
            record.metadata_path,
            kind="measurement_metadata",
            name=record.metadata_path.name,
            metadata={**artifact_metadata, "csv_file": record.csv_path.name},
        )
        self.session.record_event(
            "data_recorded",
            {
                "name": record.name,
                "csv_path": record.csv_path.name,
                "metadata_path": record.metadata_path.name,
            },
        )


def _rows_from_data(data: Any) -> list[dict[str, Any]]:
    dataframe_rows = _rows_from_dataframe_like(data)
    if dataframe_rows is not None:
        return dataframe_rows

    if isinstance(data, Mapping):
        return _rows_from_mapping(data)

    if is_dataclass(data) and not isinstance(data, type):
        return _rows_from_mapping(asdict(data))

    if _is_iterable_rows(data):
        rows = [_row_from_item(item) for item in data]
        return [_explode_row(row) for row in rows]

    return _rows_from_mapping(_row_from_item(data))


def _rows_from_dataframe_like(data: Any) -> list[dict[str, Any]] | None:
    to_dict = getattr(data, "to_dict", None)
    if not callable(to_dict):
        return None

    try:
        rows = to_dict(orient="records")
    except TypeError:
        return None

    if isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows):
        return [dict(row) for row in rows]
    return None


def _rows_from_mapping(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = dict(data)
    sequence_values = {
        key: sequence
        for key, value in values.items()
        if (sequence := _as_sequence(value)) is not None
    }
    sequence_lengths = {len(value) for value in sequence_values.values()}
    if not sequence_lengths:
        return [_explode_row(values)]
    if len(sequence_lengths) != 1:
        raise ValueError("Column-oriented data must use equal-length sequences.")

    row_count = sequence_lengths.pop()
    rows: list[dict[str, Any]] = []
    for index in range(row_count):
        row: dict[str, Any] = {}
        for key, value in values.items():
            if key in sequence_values:
                row[str(key)] = sequence_values[key][index]
            else:
                row[str(key)] = value
        rows.append(_explode_row(row))
    return rows


def _row_from_item(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {str(key): value for key, value in item.items()}
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)

    try:
        item_vars = vars(item)
    except TypeError as exc:
        raise ValueError(f"Cannot convert {type(item).__name__} to a data row.") from exc

    attrs = {
        key: value
        for key, value in item_vars.items()
        if not key.startswith("_") and not callable(value)
    }
    if attrs:
        return attrs
    raise ValueError(f"Cannot convert {type(item).__name__} to a data row.")


def _explode_row(row: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                output[f"{key}.{nested_key}"] = nested_value
        else:
            output[str(key)] = value
    return output


def _field_order(
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Sequence[str] | None,
) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()

    for field_name in fieldnames or []:
        key = str(field_name)
        if key not in seen:
            seen.add(key)
            fields.append(key)

    for row in rows:
        for key in row:
            key = str(key)
            if key not in seen:
                seen.add(key)
                fields.append(key)

    return fields


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field)) for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(_to_jsonable(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str | int | float | bool):
        return value
    return json.dumps(_to_jsonable(value), sort_keys=True)


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "tolist") and callable(value.tolist):
        return _to_jsonable(value.tolist())
    if hasattr(value, "item") and callable(value.item):
        try:
            return _to_jsonable(value.item())
        except ValueError:
            pass
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_to_jsonable(item) for item in value)
    return value


def _as_sequence(value: Any) -> list[Any] | None:
    if isinstance(value, str | bytes | bytearray | Mapping):
        return None
    if hasattr(value, "tolist") and callable(value.tolist):
        converted = value.tolist()
        if isinstance(converted, list):
            return converted
    if isinstance(value, Sequence):
        return list(value)
    return None


def _is_iterable_rows(value: Any) -> bool:
    if isinstance(value, str | bytes | bytearray | Mapping):
        return False
    if is_dataclass(value) and not isinstance(value, type):
        return False
    return isinstance(value, Iterable)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug.lower() or "record"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DataRecord",
    "DataRecorder",
    "RECORDER_SCHEMA_VERSION",
    "logger",
]
