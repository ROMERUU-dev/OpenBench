"""Experiment-aware plotting helpers for OpenBench measurement data."""

from __future__ import annotations

import csv
import json
import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal

from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from openbench.core.experiment import ExperimentResult
from openbench.data.recorder import DataRecord

logger = logging.getLogger(__name__)

AxisName = Literal["left", "right"]
PlotScale = Literal["linear", "log"]
PlotKind = Literal["line", "scatter"]
PlotInput = Mapping[str, Any] | ExperimentResult | DataRecord | Path | str


@dataclass(frozen=True)
class PlotSeries:
    """One plottable Y series inside an experiment plot template.

    Attributes:
        field: Preferred payload field used for Y values.
        label: Legend label for the series.
        axis: Target axis. Use ``"right"`` for secondary-axis quantities.
        aliases: Fallback field names accepted for compatibility with backend
            and recorder payload variations.
        color: Optional Matplotlib color.
        linestyle: Matplotlib line style.
        marker: Optional Matplotlib marker. If omitted, line plots use no marker.
        required: When True, missing data for this series makes plotting fail.
    """

    field: str
    label: str
    axis: AxisName = "left"
    aliases: tuple[str, ...] = ()
    color: str | None = None
    linestyle: str = "-"
    marker: str | None = None
    required: bool = True


@dataclass(frozen=True)
class PlotTemplate:
    """Declarative plot recipe for one OpenBench experiment type.

    A template defines how to turn an experiment payload into axes, labels, and
    one or more plotted series. Templates are intentionally data-only so the GUI,
    scripts, and tests can share the same plotting contract.

    Attributes:
        experiment_type: Stable template key, for example
            ``"chua_admittance_sweep"``.
        title: Default figure title.
        x_field: Preferred payload field used for X values.
        x_label: X-axis label.
        y_label: Primary Y-axis label.
        series: Y series plotted by this template.
        aliases: Alternative experiment names that resolve to this template.
        x_aliases: Fallback field names accepted for X values.
        y2_label: Secondary Y-axis label when any series uses ``axis="right"``.
        x_scale: Matplotlib X-axis scale.
        y_scale: Primary Y-axis scale.
        y2_scale: Secondary Y-axis scale.
        kind: Plot geometry for all series.
        figure_size: Matplotlib figure size in inches.
        grid: Whether to enable a primary-axis grid.
    """

    experiment_type: str
    title: str
    x_field: str
    x_label: str
    y_label: str
    series: tuple[PlotSeries, ...]
    aliases: tuple[str, ...] = ()
    x_aliases: tuple[str, ...] = ()
    y2_label: str | None = None
    x_scale: PlotScale = "linear"
    y_scale: PlotScale = "linear"
    y2_scale: PlotScale = "linear"
    kind: PlotKind = "line"
    figure_size: tuple[float, float] = (8.0, 4.8)
    grid: bool = True


@dataclass(frozen=True)
class PlotArtifact:
    """Descriptor for a saved OpenBench plot.

    Attributes:
        path: File path written by the plotter.
        template: Template key used to render the figure.
        format: Matplotlib output format used for the file.
        dpi: Figure DPI used during export.
    """

    path: Path
    template: str
    format: str
    dpi: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable artifact description.

        Returns:
            Dictionary describing the saved plot.
        """

        return {
            "path": str(self.path),
            "template": self.template,
            "format": self.format,
            "dpi": self.dpi,
        }


DEFAULT_PLOT_TEMPLATES: tuple[PlotTemplate, ...] = (
    PlotTemplate(
        experiment_type="dc_sweep",
        aliases=("dc", "dc-sweep", "voltage_sweep", "voltage-sweep"),
        title="DC Sweep",
        x_field="voltage_v",
        x_aliases=("voltage_setpoint_v", "input_voltage_v", "bias_voltage_v"),
        x_label="Voltage (V)",
        y_label="Current (A)",
        series=(
            PlotSeries(
                "current_a",
                "Current",
                aliases=("measured_current_a", "supply_current_a", "input_current_a"),
                color="tab:blue",
                marker="o",
            ),
            PlotSeries(
                "measured_voltage_v",
                "Measured voltage",
                axis="right",
                aliases=("output_voltage_v",),
                color="tab:orange",
                marker="s",
                required=False,
            ),
        ),
        y2_label="Measured voltage (V)",
    ),
    PlotTemplate(
        experiment_type="frequency_sweep",
        aliases=("frequency", "frequency-sweep", "bode"),
        title="Frequency Sweep",
        x_field="frequency_hz",
        x_label="Frequency (Hz)",
        y_label="Magnitude (dB)",
        series=(
            PlotSeries(
                "magnitude_db",
                "Magnitude",
                aliases=("gain_db", "amplitude_db", "response_db"),
                color="tab:blue",
            ),
            PlotSeries(
                "phase_deg",
                "Phase",
                axis="right",
                color="tab:orange",
                required=False,
            ),
        ),
        y2_label="Phase (deg)",
        x_scale="log",
    ),
    PlotTemplate(
        experiment_type="impedance_sweep",
        aliases=("impedance", "impedance-sweep", "sr860_sweep", "sr860-sweep"),
        title="Impedance Sweep",
        x_field="frequency_hz",
        x_label="Frequency (Hz)",
        y_label="Impedance magnitude (ohm)",
        series=(
            PlotSeries(
                "magnitude_ohm",
                "Magnitude",
                aliases=("impedance_ohm", "z_magnitude_ohm"),
                color="tab:blue",
            ),
            PlotSeries(
                "phase_deg",
                "Phase",
                axis="right",
                color="tab:orange",
                required=False,
            ),
        ),
        y2_label="Phase (deg)",
        x_scale="log",
    ),
    PlotTemplate(
        experiment_type="tc4069_transfer",
        aliases=("tc4069", "tc4069ubp", "tc4069-transfer", "TC4069UBP"),
        title="TC4069UBP Transfer Curve",
        x_field="input_voltage_v",
        x_label="Input voltage (V)",
        y_label="Output voltage (V)",
        series=(
            PlotSeries("output_voltage_v", "Output", color="tab:blue", marker="o"),
            PlotSeries(
                "supply_current_a",
                "Supply current",
                axis="right",
                color="tab:red",
                marker=".",
                required=False,
            ),
        ),
        y2_label="Current (A)",
    ),
    PlotTemplate(
        experiment_type="inductor_characterization",
        aliases=("inductor", "chua_inductor", "chua-inductor"),
        title="Inductor Characterization",
        x_field="frequency_hz",
        x_label="Frequency (Hz)",
        y_label="Impedance magnitude (ohm)",
        series=(
            PlotSeries("magnitude_ohm", "Magnitude", color="tab:blue"),
            PlotSeries(
                "inductance_h",
                "Inductance",
                axis="right",
                color="tab:green",
                required=False,
            ),
        ),
        y2_label="Inductance (H)",
        x_scale="log",
    ),
    PlotTemplate(
        experiment_type="chua_admittance_sweep",
        aliases=("chua", "chua_admittance", "chua-admittance"),
        title="Chua Admittance Sweep",
        x_field="bias_voltage_v",
        x_label="Bias voltage (V)",
        y_label="Admittance (S)",
        series=(
            PlotSeries("conductance_s", "Conductance G", color="tab:blue", marker="o"),
            PlotSeries(
                "susceptance_s",
                "Susceptance B",
                color="tab:orange",
                marker="s",
                required=False,
            ),
        ),
    ),
    PlotTemplate(
        experiment_type="filter_design_validation",
        aliases=(
            "filter",
            "filter_design",
            "filter-design",
            "filter_validation",
            "filter-validation",
        ),
        title="Filter Design Validation",
        x_field="frequencies_hz",
        x_label="Frequency (Hz)",
        y_label="Magnitude (dB)",
        series=(
            PlotSeries("theoretical_magnitude_db", "Theory", color="tab:blue"),
            PlotSeries(
                "measured_magnitude_db",
                "Measured",
                color="tab:orange",
                linestyle="--",
                required=False,
            ),
            PlotSeries(
                "theoretical_phase_deg",
                "Theory phase",
                axis="right",
                color="tab:green",
                linestyle=":",
                required=False,
            ),
            PlotSeries(
                "measured_phase_deg",
                "Measured phase",
                axis="right",
                color="tab:red",
                linestyle="-.",
                required=False,
            ),
        ),
        y2_label="Phase (deg)",
        x_scale="log",
    ),
)


class ExperimentPlotter:
    """Render OpenBench experiment payloads with experiment-specific templates.

    The plotter accepts live ``ExperimentResult`` instances, dictionaries
    returned by experiments, ``DataRecord`` CSV outputs, and JSON/CSV paths.
    It keeps plotting independent from instrument backends and GUI code, which
    preserves the standalone backend compatibility required by OpenBench.

    Attributes:
        default_dpi: DPI used by :meth:`save` when no explicit value is passed.
    """

    def __init__(
        self,
        templates: Iterable[PlotTemplate] | None = None,
        *,
        default_dpi: int = 160,
    ) -> None:
        """Initialize a plotter with default and optional custom templates.

        Args:
            templates: Additional templates to register after the built-ins.
                A custom template with the same key replaces the built-in one.
            default_dpi: Default export DPI for :meth:`save`.
        """

        self.default_dpi = default_dpi
        self._templates: dict[str, PlotTemplate] = {}
        self._aliases: dict[str, str] = {}
        for template in DEFAULT_PLOT_TEMPLATES:
            self.register_template(template)
        for template in templates or ():
            self.register_template(template)

    def register_template(self, template: PlotTemplate) -> None:
        """Register or replace an experiment plot template.

        Args:
            template: Template to add to the registry.

        Raises:
            ValueError: If the template key, axis field, or series list is empty.
        """

        if not template.experiment_type.strip():
            raise ValueError("Plot template experiment_type must not be empty.")
        if not template.x_field.strip():
            raise ValueError("Plot template x_field must not be empty.")
        if not template.series:
            raise ValueError("Plot template must define at least one series.")

        key = _normalize_key(template.experiment_type)
        self._templates[key] = template
        self._aliases[key] = key
        for alias in template.aliases:
            if alias.strip():
                self._aliases[_normalize_key(alias)] = key
        logger.debug("Registered plot template: %s", template.experiment_type)

    def templates(self) -> tuple[PlotTemplate, ...]:
        """Return registered plot templates.

        Returns:
            Templates currently available in registry order.
        """

        return tuple(self._templates.values())

    def template_for(
        self,
        source: PlotInput,
        *,
        experiment_type: str | None = None,
    ) -> PlotTemplate:
        """Resolve the best template for a payload or requested experiment type.

        Args:
            source: Experiment payload, result, data record, or file path.
            experiment_type: Optional explicit template key or alias.

        Returns:
            Selected plot template.

        Raises:
            ValueError: If no registered template matches the input.
        """

        payload = _payload_from_source(source)
        return self._resolve_template(payload, experiment_type=experiment_type)

    def plot(
        self,
        source: PlotInput,
        *,
        experiment_type: str | None = None,
        template: PlotTemplate | str | None = None,
        title: str | None = None,
    ) -> Figure:
        """Create a Matplotlib figure for an OpenBench experiment payload.

        Args:
            source: Experiment payload, result, data record, or file path.
            experiment_type: Optional template key or alias used when
                auto-detection is ambiguous.
            template: Optional template object or registered template key. When
                supplied, it takes precedence over ``experiment_type``.
            title: Optional figure title override.

        Returns:
            Matplotlib figure containing the rendered experiment plot.

        Raises:
            ValueError: If no template can be selected or no plottable data is
                found for the selected template.
        """

        payload = _payload_from_source(source)
        resolved = self._resolve_template(
            payload,
            experiment_type=experiment_type,
            template=template,
        )
        return self._plot_payload(payload, resolved, title=title)

    def save(
        self,
        source: PlotInput,
        output_path: Path | str,
        *,
        experiment_type: str | None = None,
        template: PlotTemplate | str | None = None,
        title: str | None = None,
        dpi: int | None = None,
        image_format: str | None = None,
    ) -> PlotArtifact:
        """Render and save a plot file.

        Args:
            source: Experiment payload, result, data record, or file path.
            output_path: Destination file path. The parent directory is created.
            experiment_type: Optional template key or alias used when
                auto-detection is ambiguous.
            template: Optional template object or registered template key. When
                supplied, it takes precedence over ``experiment_type``.
            title: Optional figure title override.
            dpi: Export DPI. Defaults to ``default_dpi``.
            image_format: Matplotlib output format. Defaults to the destination
                file extension, or PNG when the path has no extension.

        Returns:
            Descriptor for the saved plot artifact.
        """

        payload = _payload_from_source(source)
        resolved = self._resolve_template(
            payload,
            experiment_type=experiment_type,
            template=template,
        )
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        fmt = image_format or output.suffix.lstrip(".") or "png"
        export_dpi = dpi or self.default_dpi
        fig = self._plot_payload(payload, resolved, title=title)
        fig.savefig(output, dpi=export_dpi, format=fmt, bbox_inches="tight")
        plt.close(fig)

        artifact = PlotArtifact(
            path=output,
            template=resolved.experiment_type,
            format=fmt,
            dpi=export_dpi,
        )
        logger.info("Saved plot: %s template=%s", output, resolved.experiment_type)
        return artifact

    def _resolve_template(
        self,
        payload: Mapping[str, Any],
        *,
        experiment_type: str | None = None,
        template: PlotTemplate | str | None = None,
    ) -> PlotTemplate:
        if isinstance(template, PlotTemplate):
            return template
        if isinstance(template, str):
            return self._template_by_key(template)
        if experiment_type is not None:
            return self._template_by_key(experiment_type)

        inferred = _infer_experiment_type(payload)
        if inferred is not None:
            try:
                return self._template_by_key(inferred)
            except ValueError:
                logger.debug("Inferred plot template is not registered: %s", inferred)

        field_names = _available_fields(payload)
        for candidate in self._templates.values():
            x_field = _resolve_field_name(field_names, candidate.x_field, candidate.x_aliases)
            required = [series for series in candidate.series if series.required]
            if x_field in field_names and all(
                _resolve_field_name(field_names, series.field, series.aliases) in field_names
                for series in required
            ):
                return candidate

        raise ValueError(
            "Could not infer plot template. Pass experiment_type=... or register a template."
        )

    def _template_by_key(self, value: str) -> PlotTemplate:
        key = _normalize_key(value)
        template_key = self._aliases.get(key)
        if template_key is None:
            available = ", ".join(template.experiment_type for template in self._templates.values())
            raise ValueError(f"Unknown plot template {value!r}. Available templates: {available}")
        return self._templates[template_key]

    def _plot_payload(
        self,
        payload: Mapping[str, Any],
        template: PlotTemplate,
        *,
        title: str | None,
    ) -> Figure:
        rows = _rows_from_payload(payload)
        field_names = _available_fields(payload)
        x_field = _resolve_field_name(field_names, template.x_field, template.x_aliases)

        fig, primary = plt.subplots(figsize=template.figure_size)
        secondary: Axes | None = None
        if any(series.axis == "right" for series in template.series):
            secondary = primary.twinx()

        plotted = 0
        for series in template.series:
            y_field = _resolve_field_name(field_names, series.field, series.aliases)
            target = secondary if series.axis == "right" and secondary is not None else primary
            points = _series_points(
                payload,
                rows,
                x_field=x_field,
                y_field=y_field,
                require_positive_x=template.x_scale == "log",
                require_positive_y=(
                    template.y2_scale == "log"
                    if series.axis == "right"
                    else template.y_scale == "log"
                ),
            )
            if not points:
                if series.required:
                    raise ValueError(
                        "No plottable data for required series "
                        f"{series.field!r} in template {template.experiment_type!r}."
                    )
                logger.debug(
                    "Skipping optional plot series without data: %s.%s",
                    template.experiment_type,
                    series.field,
                )
                continue

            x_values = [point[0] for point in points]
            y_values = [point[1] for point in points]
            if template.kind == "scatter":
                target.scatter(
                    x_values,
                    y_values,
                    label=series.label,
                    color=series.color,
                    marker=series.marker or "o",
                )
            else:
                target.plot(
                    x_values,
                    y_values,
                    label=series.label,
                    color=series.color,
                    linestyle=series.linestyle,
                    marker=series.marker,
                )
            plotted += 1

        if plotted == 0:
            raise ValueError(f"No plottable data for template {template.experiment_type!r}.")

        primary.set_title(title or template.title)
        primary.set_xlabel(template.x_label)
        primary.set_ylabel(template.y_label)
        primary.set_xscale(template.x_scale)
        primary.set_yscale(template.y_scale)
        if template.grid:
            primary.grid(True, which="both", alpha=0.25)

        if secondary is not None:
            secondary.set_ylabel(template.y2_label or template.y_label)
            secondary.set_yscale(template.y2_scale)

        _apply_legend(primary, secondary)
        fig.tight_layout()
        return fig


def _payload_from_source(source: PlotInput) -> Mapping[str, Any]:
    if isinstance(source, ExperimentResult):
        return dict(source.data)
    if isinstance(source, DataRecord):
        return _payload_from_data_record(source)
    if isinstance(source, Path):
        return _payload_from_path(source)
    if isinstance(source, str):
        path = Path(source)
        if path.exists():
            return _payload_from_path(path)
        raise ValueError(f"Plot input string is not an existing path: {source}")
    if isinstance(source, Mapping):
        return _unwrap_payload(source)
    raise ValueError(f"Unsupported plot input type: {type(source).__name__}")


def _payload_from_data_record(record: DataRecord) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "points": _read_csv_rows(record.csv_path),
        "record": record.to_dict(),
    }
    if record.metadata_path.exists():
        try:
            metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Could not parse recorder metadata: %s", record.metadata_path)
            metadata = {}
        payload["metadata"] = metadata
        for key in ("experiment", "component", "experiment_type", "template"):
            value = _metadata_value(metadata, key)
            if value is not None:
                payload[key] = value
    return payload


def _payload_from_path(path: Path) -> Mapping[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"JSON plot input must contain an object: {path}")
        return _unwrap_payload(payload)
    return {"points": _read_csv_rows(path), "source_file": str(path)}


def _unwrap_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = payload.get("data")
    if isinstance(inner, Mapping) and (
        "state" in payload
        or "duration_s" in payload
        or "error" in payload
        or "experiment" in payload
    ):
        output = dict(inner)
        if "experiment" not in output and isinstance(payload.get("experiment"), str):
            output["experiment"] = payload["experiment"]
        return output
    return payload


def _metadata_value(metadata: Mapping[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    nested = metadata.get("metadata")
    if isinstance(nested, Mapping) and key in nested:
        return nested[key]
    return None


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _infer_experiment_type(payload: Mapping[str, Any]) -> str | None:
    for key in ("experiment_type", "template", "experiment", "component"):
        value = payload.get(key)
        if isinstance(value, str):
            inferred = _infer_from_name(value)
            if inferred is not None:
                return inferred

    if "filter_design" in payload and "validation" in payload:
        return "filter_design_validation"

    fields = _available_fields(payload)
    if {"bias_voltage_v", "conductance_s"} <= fields:
        return "chua_admittance_sweep"
    if {"input_voltage_v", "output_voltage_v"} <= fields:
        return "tc4069_transfer"
    if {"frequency_hz", "inductance_h"} <= fields:
        return "inductor_characterization"
    if {"frequency_hz", "magnitude_ohm"} <= fields:
        return "impedance_sweep"
    if {"frequencies_hz", "theoretical_magnitude_db"} <= fields:
        return "filter_design_validation"
    if {"frequency_hz", "magnitude_db"} <= fields:
        return "frequency_sweep"
    if {"voltage_v", "current_a"} <= fields:
        return "dc_sweep"
    return None


def _infer_from_name(value: str) -> str | None:
    key = _normalize_key(value)
    if "chua" in key and "admittance" in key:
        return "chua_admittance_sweep"
    if "tc4069" in key:
        return "tc4069_transfer"
    if "inductor" in key:
        return "inductor_characterization"
    if "filter" in key:
        return "filter_design_validation"
    if "impedance" in key:
        return "impedance_sweep"
    if "frequency" in key or "bode" in key:
        return "frequency_sweep"
    if "dc" in key or "voltage_sweep" in key:
        return "dc_sweep"
    return None


def _rows_from_payload(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    points = payload.get("points")
    if points is not None:
        return [_row_from_item(point) for point in _as_sequence(points) or []]

    dataframe_rows = _rows_from_dataframe_like(payload)
    if dataframe_rows is not None:
        return dataframe_rows

    return []


def _rows_from_dataframe_like(data: Any) -> list[Mapping[str, Any]] | None:
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


def _row_from_item(item: Any) -> Mapping[str, Any]:
    if isinstance(item, Mapping):
        return item
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    try:
        return {
            str(key): value
            for key, value in vars(item).items()
            if not key.startswith("_") and not callable(value)
        }
    except TypeError as exc:
        raise ValueError(f"Cannot convert {type(item).__name__} to plot row.") from exc


def _available_fields(payload: Mapping[str, Any]) -> set[str]:
    fields = {str(key) for key in payload.keys()}
    for row in _rows_from_payload(payload):
        fields.update(str(key) for key in row.keys())
    return fields


def _resolve_field_name(
    available_fields: set[str],
    field: str,
    aliases: Sequence[str],
) -> str:
    for candidate in (field, *aliases):
        if candidate in available_fields:
            return candidate
    return field


def _series_points(
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    x_field: str,
    y_field: str,
    require_positive_x: bool,
    require_positive_y: bool,
) -> list[tuple[float, float]]:
    if rows:
        raw_pairs = (
            (_value_from_mapping(row, x_field), _value_from_mapping(row, y_field))
            for row in rows
        )
    else:
        x_values = _as_sequence(_value_from_mapping(payload, x_field))
        y_values = _as_sequence(_value_from_mapping(payload, y_field))
        if x_values is None or y_values is None:
            return []
        raw_pairs = zip(x_values, y_values, strict=False)

    points: list[tuple[float, float]] = []
    for raw_x, raw_y in raw_pairs:
        x = _to_finite_float(raw_x)
        y = _to_finite_float(raw_y)
        if x is None or y is None:
            continue
        if require_positive_x and x <= 0.0:
            continue
        if require_positive_y and y <= 0.0:
            continue
        points.append((x, y))
    return points


def _value_from_mapping(data: Mapping[str, Any], field: str) -> Any:
    if field in data:
        return data[field]
    current: Any = data
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _as_sequence(value: Any) -> list[Any] | None:
    if value is None or isinstance(value, str | bytes | bytearray):
        return None
    if hasattr(value, "tolist") and callable(value.tolist):
        converted = value.tolist()
        if isinstance(converted, list):
            return converted
    if isinstance(value, Sequence):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return list(value)
    return None


def _to_finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _apply_legend(primary: Axes, secondary: Axes | None) -> None:
    handles, labels = primary.get_legend_handles_labels()
    if secondary is not None:
        right_handles, right_labels = secondary.get_legend_handles_labels()
        handles.extend(right_handles)
        labels.extend(right_labels)
    if handles:
        primary.legend(handles, labels, loc="best")


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


__all__ = [
    "DEFAULT_PLOT_TEMPLATES",
    "ExperimentPlotter",
    "PlotArtifact",
    "PlotSeries",
    "PlotTemplate",
    "logger",
]
