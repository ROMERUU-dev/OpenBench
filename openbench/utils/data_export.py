"""Structured data export helpers for large OpenBench datasets."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import h5py
import numpy as np

logger = logging.getLogger(__name__)

HDF5_SCHEMA_VERSION = 1
DEFAULT_HDF5_COMPRESSION = "gzip"
DEFAULT_HDF5_COMPRESSION_OPTS = 4


@dataclass(frozen=True)
class HDF5ExportRecord:
    """Descriptor for an HDF5 dataset export.

    Attributes:
        name: Human-readable dataset name supplied by the caller.
        hdf5_path: Path to the written HDF5 file.
        row_count: Number of rows inferred from non-scalar datasets.
        fields: Ordered exported field names.
        created_at: UTC timestamp when the file was created.
        metadata: User metadata embedded in the HDF5 root attributes.
        sha256: SHA-256 digest of the final HDF5 file.
        compression: Compression filter used for non-scalar datasets.
    """

    name: str
    hdf5_path: Path
    row_count: int
    fields: tuple[str, ...]
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)
    sha256: str = ""
    compression: str | None = DEFAULT_HDF5_COMPRESSION

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable export description.

        Returns:
            Dictionary describing the HDF5 file and embedded dataset metadata.
        """

        return {
            "name": self.name,
            "hdf5_path": str(self.hdf5_path),
            "row_count": self.row_count,
            "fields": list(self.fields),
            "created_at": _to_jsonable(self.created_at),
            "metadata": _to_jsonable(self.metadata),
            "sha256": self.sha256,
            "compression": self.compression,
        }


class HDF5Exporter:
    """Write columnar OpenBench measurement data to HDF5.

    The exporter stores each field as a dataset under ``/data`` and embeds
    metadata in root attributes. Column-oriented mappings and NumPy arrays are
    written directly so large measurements avoid the CSV row materialization
    path. Row-oriented iterables remain supported for compatibility with
    existing experiment payloads.

    Attributes:
        compression: HDF5 compression filter for non-scalar datasets.
        compression_opts: Optional compression level for gzip datasets.
        chunked: Whether non-scalar datasets should be chunked.
        require_equal_length: Whether vector datasets must share the same first
            dimension.
    """

    def __init__(
        self,
        *,
        compression: str | None = DEFAULT_HDF5_COMPRESSION,
        compression_opts: int | None = DEFAULT_HDF5_COMPRESSION_OPTS,
        chunked: bool = True,
        require_equal_length: bool = True,
    ) -> None:
        """Initialize an HDF5 exporter.

        Args:
            compression: HDF5 compression filter such as ``"gzip"`` or
                ``"lzf"``. Use ``None`` to disable compression.
            compression_opts: Compression level for gzip datasets.
            chunked: Enable chunked storage for non-scalar datasets.
            require_equal_length: Require all non-scalar fields to share the
                same first dimension.
        """

        self.compression = compression
        self.compression_opts = compression_opts
        self.chunked = chunked
        self.require_equal_length = require_equal_length

    def export(
        self,
        path: Path | str,
        name: str,
        data: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
        fieldnames: Sequence[str] | None = None,
    ) -> HDF5ExportRecord:
        """Persist a dataset to an HDF5 file.

        Args:
            path: Destination ``.h5`` or ``.hdf5`` path.
            name: Human-readable dataset name.
            data: Column-oriented mapping, row-oriented iterable, dataclass, or
                dataframe-like object to export.
            metadata: Structured metadata embedded in the HDF5 root attributes.
            fieldnames: Optional preferred field order. Additional inferred
                fields are appended after this order.

        Returns:
            Descriptor for the written HDF5 export.

        Raises:
            ValueError: If ``name`` is empty, no fields can be inferred, or
                non-scalar fields have different lengths while equal lengths are
                required.
        """

        if not name.strip():
            raise ValueError("Export name must not be empty.")

        output_path = Path(path)
        columns = _columns_from_data(data)
        fields = _field_order(columns, fieldnames)
        if not fields:
            raise ValueError("Cannot write HDF5 without fields.")

        row_count = _resolve_row_count(columns, self.require_equal_length)
        for field_name in fields:
            columns.setdefault(field_name, [None] * row_count)
        created_at = datetime.now(UTC)
        export_metadata = _to_jsonable(metadata or {})

        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Exporting HDF5 dataset: %s path=%s rows=%d fields=%d",
            name,
            output_path,
            row_count,
            len(fields),
        )

        with h5py.File(output_path, "w", track_order=True) as h5file:
            self._write_root_attrs(
                h5file,
                name=name,
                created_at=created_at,
                row_count=row_count,
                fields=fields,
                metadata=export_metadata,
            )
            data_group = h5file.create_group("data", track_order=True)
            data_group.attrs["row_count"] = row_count
            data_group.attrs["fields_json"] = json.dumps(list(fields), sort_keys=False)

            used_names: set[str] = set()
            for field_name in fields:
                dataset_name = _safe_hdf5_name(field_name, used_names)
                payload, dtype = _dataset_payload(columns[field_name])
                create_kwargs = self._dataset_create_kwargs(payload)
                if dtype is not None:
                    create_kwargs["dtype"] = dtype
                dataset = data_group.create_dataset(dataset_name, data=payload, **create_kwargs)
                dataset.attrs["field_name"] = field_name

        sha256 = _sha256_file(output_path)
        record = HDF5ExportRecord(
            name=name,
            hdf5_path=output_path,
            row_count=row_count,
            fields=tuple(fields),
            created_at=created_at,
            metadata=export_metadata,
            sha256=sha256,
            compression=self.compression,
        )
        logger.debug("HDF5 dataset exported: %s", record.to_dict())
        return record

    def _write_root_attrs(
        self,
        h5file: h5py.File,
        *,
        name: str,
        created_at: datetime,
        row_count: int,
        fields: Sequence[str],
        metadata: Mapping[str, Any],
    ) -> None:
        h5file.attrs["schema_version"] = HDF5_SCHEMA_VERSION
        h5file.attrs["format"] = "hdf5"
        h5file.attrs["name"] = name
        h5file.attrs["created_at"] = _to_jsonable(created_at)
        h5file.attrs["row_count"] = row_count
        h5file.attrs["field_count"] = len(fields)
        h5file.attrs["fields_json"] = json.dumps(list(fields), sort_keys=False)
        h5file.attrs["metadata_json"] = json.dumps(_to_jsonable(metadata), sort_keys=True)
        h5file.attrs["compression"] = self.compression or ""

    def _dataset_create_kwargs(self, payload: Any) -> dict[str, Any]:
        array = np.asarray(payload)
        if array.shape == () or 0 in array.shape:
            return {}

        kwargs: dict[str, Any] = {}
        if self.chunked:
            kwargs["chunks"] = True
        if self.compression is not None:
            kwargs["compression"] = self.compression
            if self.compression == "gzip" and self.compression_opts is not None:
                kwargs["compression_opts"] = self.compression_opts
            if array.dtype.kind in "biufc":
                kwargs["shuffle"] = True
        return kwargs


def export_hdf5(
    path: Path | str,
    name: str,
    data: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
    fieldnames: Sequence[str] | None = None,
    compression: str | None = DEFAULT_HDF5_COMPRESSION,
    compression_opts: int | None = DEFAULT_HDF5_COMPRESSION_OPTS,
    chunked: bool = True,
    require_equal_length: bool = True,
) -> HDF5ExportRecord:
    """Write measurement data to an HDF5 file.

    Args:
        path: Destination ``.h5`` or ``.hdf5`` path.
        name: Human-readable dataset name.
        data: Column-oriented mapping, row-oriented iterable, dataclass, or
            dataframe-like object to export.
        metadata: Structured metadata embedded in the HDF5 root attributes.
        fieldnames: Optional preferred field order.
        compression: HDF5 compression filter for non-scalar datasets.
        compression_opts: Optional compression level for gzip datasets.
        chunked: Enable chunked storage for non-scalar datasets.
        require_equal_length: Require all non-scalar fields to share a first
            dimension.

    Returns:
        Descriptor for the written HDF5 export.
    """

    exporter = HDF5Exporter(
        compression=compression,
        compression_opts=compression_opts,
        chunked=chunked,
        require_equal_length=require_equal_length,
    )
    return exporter.export(
        path,
        name,
        data,
        metadata=metadata,
        fieldnames=fieldnames,
    )


def _columns_from_data(data: Any) -> dict[str, Any]:
    dataframe_columns = _columns_from_dataframe_like(data)
    if dataframe_columns is not None:
        return dataframe_columns

    if isinstance(data, Mapping):
        return _flatten_mapping(data)

    if is_dataclass(data) and not isinstance(data, type):
        return _flatten_mapping(asdict(data))

    if _is_iterable_rows(data):
        return _columns_from_rows(data)

    return _flatten_mapping(_row_from_item(data))


def _columns_from_dataframe_like(data: Any) -> dict[str, Any] | None:
    columns_attr = getattr(data, "columns", None)
    if columns_attr is None:
        return None

    columns: dict[str, Any] = {}
    for column_name in columns_attr:
        try:
            column = data[column_name]
        except (KeyError, TypeError):
            return None
        to_numpy = getattr(column, "to_numpy", None)
        columns[str(column_name)] = to_numpy() if callable(to_numpy) else column
    return columns


def _columns_from_rows(data: Iterable[Any]) -> dict[str, list[Any]]:
    columns: dict[str, list[Any]] = {}
    row_count = 0
    for item in data:
        row = _flatten_mapping(_row_from_item(item))
        for existing_values in columns.values():
            existing_values.append(None)

        for key, value in row.items():
            if key not in columns:
                columns[key] = [None] * row_count
                columns[key].append(value)
            else:
                columns[key][-1] = value
        row_count += 1
    return columns


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


def _flatten_mapping(mapping: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in mapping.items():
        field_name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            output.update(_flatten_mapping(value, field_name))
        else:
            output[field_name] = value
    return output


def _field_order(columns: Mapping[str, Any], fieldnames: Sequence[str] | None) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()

    for field_name in fieldnames or []:
        key = str(field_name)
        if key not in seen:
            seen.add(key)
            fields.append(key)

    for key in columns:
        field_name = str(key)
        if field_name not in seen:
            seen.add(field_name)
            fields.append(field_name)
    return fields


def _resolve_row_count(columns: Mapping[str, Any], require_equal_length: bool) -> int:
    lengths = {
        field_name: length
        for field_name, value in columns.items()
        if (length := _first_dimension(value)) is not None
    }
    if not lengths:
        return 1 if columns else 0

    unique_lengths = set(lengths.values())
    if require_equal_length and len(unique_lengths) != 1:
        detail = ", ".join(f"{field}={length}" for field, length in lengths.items())
        raise ValueError(f"HDF5 columns must have equal first dimensions: {detail}")
    return max(unique_lengths)


def _first_dimension(value: Any) -> int | None:
    if isinstance(value, str | bytes | bytearray | Mapping):
        return None

    array = _as_array_like(value)
    if array is not None:
        if array.shape == ():
            return None
        return int(array.shape[0])

    if isinstance(value, Sequence):
        return len(value)
    return None


def _dataset_payload(value: Any) -> tuple[Any, Any | None]:
    array = _as_array_like(value)
    if array is not None:
        return _array_payload(array)

    jsonable = _to_jsonable(value)
    if isinstance(jsonable, str):
        return jsonable, h5py.string_dtype(encoding="utf-8")
    if jsonable is None:
        return "", h5py.string_dtype(encoding="utf-8")
    if isinstance(jsonable, int | float | complex | bool):
        return jsonable, None
    return json.dumps(jsonable, sort_keys=True), h5py.string_dtype(encoding="utf-8")


def _array_payload(array: np.ndarray) -> tuple[Any, Any | None]:
    if array.dtype.kind in "SUO":
        string_array = np.asarray(
            [_string_cell(item) for item in array.reshape(-1)],
            dtype=object,
        ).reshape(array.shape)
        return string_array, h5py.string_dtype(encoding="utf-8")
    return array, None


def _as_array_like(value: Any) -> np.ndarray | None:
    if isinstance(value, str | bytes | bytearray | Mapping):
        return None
    if isinstance(value, np.ndarray):
        return value
    to_numpy = getattr(value, "to_numpy", None)
    if callable(to_numpy):
        return np.asarray(to_numpy())
    if hasattr(value, "tolist") and callable(value.tolist):
        return np.asarray(value.tolist())
    if isinstance(value, Sequence):
        return np.asarray(list(value))
    return None


def _string_cell(value: Any) -> str:
    jsonable = _to_jsonable(value)
    if jsonable is None:
        return ""
    if isinstance(jsonable, str):
        return jsonable
    if isinstance(jsonable, int | float | complex | bool):
        return str(jsonable)
    return json.dumps(jsonable, sort_keys=True)


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


def _is_iterable_rows(value: Any) -> bool:
    if isinstance(value, str | bytes | bytearray | Mapping):
        return False
    if is_dataclass(value) and not isinstance(value, type):
        return False
    if _as_array_like(value) is not None:
        return False
    return isinstance(value, Iterable)


def _safe_hdf5_name(value: str, used_names: set[str]) -> str:
    base = value.replace("/", "_").replace("\x00", "").strip() or "field"
    candidate = base
    index = 1
    while candidate in used_names:
        index += 1
        candidate = f"{base}_{index}"
    used_names.add(candidate)
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DEFAULT_HDF5_COMPRESSION",
    "DEFAULT_HDF5_COMPRESSION_OPTS",
    "HDF5ExportRecord",
    "HDF5Exporter",
    "HDF5_SCHEMA_VERSION",
    "export_hdf5",
    "logger",
]
