"""Live Matplotlib plotting panel for streaming OpenBench measurements."""

from __future__ import annotations

import logging
import math
import random
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import customtkinter as ctk
from matplotlib.animation import FuncAnimation
from matplotlib.figure import Figure

from openbench.gui.panels.content_panel import ContentPanel
from openbench.gui.theme import theme_manager

logger = logging.getLogger(__name__)

_DEFAULT_SERIES = "measurement"
_DEFAULT_MAX_POINTS = 1_000
_DEFAULT_ANIMATION_INTERVAL_MS = 250
_DEMO_INTERVAL_MS = 200


@dataclass(frozen=True)
class LiveSeriesConfig:
    """Display configuration for one live plot series.

    Attributes:
        key: Stable series identifier used when appending points.
        label: Legend label displayed on the plot.
        color: Optional Matplotlib color. When omitted, the panel assigns a
            theme-aware default color.
        linestyle: Matplotlib line style.
        marker: Optional Matplotlib marker.
    """

    key: str
    label: str
    color: str | None = None
    linestyle: str = "-"
    marker: str | None = None


class LivePlotBuffer:
    """Bounded in-memory buffer for live plot samples.

    The buffer is intentionally independent from CustomTkinter and Matplotlib
    so experiment code, tests, and backend adapters can feed it without GUI
    dependencies.

    Args:
        max_points: Maximum number of samples retained per series.
    """

    def __init__(self, max_points: int = _DEFAULT_MAX_POINTS) -> None:
        if max_points <= 0:
            raise ValueError("max_points must be greater than zero.")
        self._max_points = max_points
        self._series: dict[str, deque[tuple[float, float]]] = {}

    @property
    def max_points(self) -> int:
        """Maximum number of samples retained per series."""

        return self._max_points

    def append(self, series: str, x: float, y: float) -> None:
        """Append a single finite point to a series.

        Args:
            series: Series identifier. Empty values are normalized to
                ``"measurement"``.
            x: X coordinate.
            y: Y coordinate.

        Raises:
            ValueError: If either coordinate is not finite.
        """

        point = (_finite_float(x, name="x"), _finite_float(y, name="y"))
        key = _series_key(series)
        self._series.setdefault(key, deque(maxlen=self._max_points)).append(point)
        logger.debug("LivePlotBuffer appended series=%s x=%s y=%s", key, point[0], point[1])

    def extend(self, series: str, points: Iterable[tuple[float, float]]) -> int:
        """Append multiple points to a series.

        Args:
            series: Series identifier.
            points: Iterable of ``(x, y)`` pairs.

        Returns:
            Number of points appended.
        """

        count = 0
        for x, y in points:
            self.append(series, x, y)
            count += 1
        return count

    def replace(self, series: str, points: Iterable[tuple[float, float]]) -> int:
        """Replace all points for one series.

        Args:
            series: Series identifier.
            points: Iterable of ``(x, y)`` pairs.

        Returns:
            Number of points stored after replacement.
        """

        key = _series_key(series)
        self.clear(key)
        return self.extend(key, points)

    def append_mapping(
        self,
        sample: Mapping[str, Any],
        *,
        x_field: str = "x",
        y_field: str = "y",
        series: str = _DEFAULT_SERIES,
    ) -> bool:
        """Append a mapping sample when required fields are present and finite.

        Args:
            sample: Mapping containing numeric X/Y values.
            x_field: Mapping key containing the X coordinate.
            y_field: Mapping key containing the Y coordinate.
            series: Series identifier used for the appended point.

        Returns:
            ``True`` when a point was appended, otherwise ``False``.
        """

        if x_field not in sample or y_field not in sample:
            logger.debug("Live sample missing fields x=%s y=%s", x_field, y_field)
            return False
        try:
            self.append(series, sample[x_field], sample[y_field])
        except ValueError:
            logger.debug("Live sample rejected as non-finite: %s", sample)
            return False
        return True

    def clear(self, series: str | None = None) -> None:
        """Clear one series or all buffered series.

        Args:
            series: Optional series identifier. When omitted, all data is
                removed.
        """

        if series is None:
            self._series.clear()
            logger.debug("LivePlotBuffer cleared all series")
            return
        self._series.pop(_series_key(series), None)
        logger.debug("LivePlotBuffer cleared series=%s", series)

    def points(self, series: str) -> tuple[tuple[float, float], ...]:
        """Return immutable points for a series.

        Args:
            series: Series identifier.

        Returns:
            Tuple of ``(x, y)`` points.
        """

        return tuple(self._series.get(_series_key(series), ()))

    def series_keys(self) -> tuple[str, ...]:
        """Return the series keys currently present in insertion order."""

        return tuple(self._series.keys())


class LivePlotPanel(ContentPanel):
    """CustomTkinter content panel with animated Matplotlib live plotting.

    The panel exposes a small public API for experiment runners and backend
    adapters to push numeric samples without depending on specific instruments.
    A retained :class:`matplotlib.animation.FuncAnimation` keeps the plot
    refreshed while the panel is visible.

    Args:
        master: Parent CustomTkinter widget.
        series: Optional series display definitions.
        max_points: Maximum samples retained per series.
        animation_interval_ms: Animation update interval in milliseconds.
        title: Plot title.
        x_label: X-axis label.
        y_label: Y-axis label.
        **kwargs: Forwarded to ``ContentPanel``.
    """

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        series: Iterable[LiveSeriesConfig] | None = None,
        *,
        max_points: int = _DEFAULT_MAX_POINTS,
        animation_interval_ms: int = _DEFAULT_ANIMATION_INTERVAL_MS,
        title: str = "Live Measurements",
        x_label: str = "Time (s)",
        y_label: str = "Value",
        **kwargs: Any,
    ) -> None:
        self._buffer = LivePlotBuffer(max_points=max_points)
        self._series_configs = _series_config_map(series)
        self._animation_interval_ms = animation_interval_ms
        self._plot_title = title
        self._x_label = x_label
        self._y_label = y_label
        self._running = True
        self._autoscale = True
        self._demo_running = False
        self._demo_start = time.monotonic()
        self._demo_job: str | None = None
        self._canvas: Any | None = None
        self._animation: FuncAnimation | None = None
        self._lines: dict[str, Any] = {}
        super().__init__(master, **kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_point(self, x: float, y: float, *, series: str = _DEFAULT_SERIES) -> None:
        """Append one live data point.

        Args:
            x: X coordinate.
            y: Y coordinate.
            series: Series identifier. Unknown series are added dynamically.
        """

        key = _series_key(series)
        self._ensure_series_config(key)
        self._buffer.append(key, x, y)
        self._update_status()

    def add_point(self, x: float, y: float, *, series: str = _DEFAULT_SERIES) -> None:
        """Alias for :meth:`append_point` used by simple backend callbacks.

        Args:
            x: X coordinate.
            y: Y coordinate.
            series: Series identifier.
        """

        self.append_point(x, y, series=series)

    def append_sample(
        self,
        sample: Mapping[str, Any],
        *,
        x_field: str = "x",
        y_field: str = "y",
        series: str = _DEFAULT_SERIES,
    ) -> bool:
        """Append a mapping sample when it contains finite X/Y fields.

        Args:
            sample: Mapping with numeric coordinate values.
            x_field: Key containing the X coordinate.
            y_field: Key containing the Y coordinate.
            series: Series identifier.

        Returns:
            ``True`` when a point was appended, otherwise ``False``.
        """

        key = _series_key(series)
        appended = self._buffer.append_mapping(
            sample,
            x_field=x_field,
            y_field=y_field,
            series=key,
        )
        if appended:
            self._ensure_series_config(key)
            self._update_status()
        return appended

    def extend_points(
        self,
        points: Iterable[tuple[float, float]],
        *,
        series: str = _DEFAULT_SERIES,
    ) -> int:
        """Append multiple live data points.

        Args:
            points: Iterable of ``(x, y)`` pairs.
            series: Series identifier.

        Returns:
            Number of points appended.
        """

        key = _series_key(series)
        self._ensure_series_config(key)
        appended = self._buffer.extend(key, points)
        self._update_status()
        return appended

    def set_series_data(
        self,
        series: str,
        points: Iterable[tuple[float, float]],
    ) -> int:
        """Replace one series with a new point set.

        Args:
            series: Series identifier.
            points: Iterable of ``(x, y)`` pairs.

        Returns:
            Number of points stored.
        """

        key = _series_key(series)
        self._ensure_series_config(key)
        stored = self._buffer.replace(key, points)
        self._sync_lines()
        self._update_status()
        return stored

    def clear(self) -> None:
        """Remove all live data from the plot."""

        self._buffer.clear()
        for line in self._lines.values():
            line.set_data([], [])
        if hasattr(self, "_axis"):
            self._rescale_axes()
        self._draw_idle()
        self._update_status()
        logger.info("LivePlotPanel cleared")

    def start_animation(self) -> None:
        """Start live plot animation updates."""

        self.resume()

    def stop_animation(self) -> None:
        """Stop live plot animation updates."""

        self.pause()

    def resume(self) -> None:
        """Resume the Matplotlib animation event source."""

        self._running = True
        self._start_animation_event_source()
        self._update_status()
        logger.info("LivePlotPanel animation resumed")

    def pause(self) -> None:
        """Pause the Matplotlib animation event source."""

        self._running = False
        self._stop_animation_event_source()
        self._update_status()
        logger.info("LivePlotPanel animation paused")

    def is_running(self) -> bool:
        """Return whether animation updates are enabled."""

        return self._running

    def set_autoscale(self, enabled: bool) -> None:
        """Enable or disable automatic axis scaling.

        Args:
            enabled: ``True`` to autoscale after each animation frame.
        """

        self._autoscale = enabled
        if self._autoscale:
            self._rescale_axes()
        self._update_status()
        logger.info("LivePlotPanel autoscale=%s", enabled)

    # ------------------------------------------------------------------
    # ContentPanel lifecycle
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        """Resume animation when this panel becomes visible."""

        super().on_show()
        if self._running:
            self.resume()

    def on_hide(self) -> None:
        """Suspend animation timers and simulated feed while hidden."""

        super().on_hide()
        self._stop_animation_event_source()
        self._stop_demo_feed()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        colors = theme_manager.get_colors()
        ff = str(theme_manager.get_font("family_fallback"))

        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent", height=76)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 0))
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Live Plot",
            font=(ff, 22, "bold"),
            text_color=colors["text_primary"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self._status_label = ctk.CTkLabel(
            header,
            text="",
            font=(ff, 12),
            text_color=colors["text_muted"],
            anchor="w",
        )
        self._status_label.grid(row=1, column=0, sticky="w")

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, rowspan=2, sticky="e")

        self._pause_button = ctk.CTkButton(
            controls,
            text="Pause",
            width=72,
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self._toggle_running,
        )
        self._pause_button.grid(row=0, column=0, padx=(0, 8))

        self._demo_button = ctk.CTkButton(
            controls,
            text="Simulate",
            width=82,
            height=32,
            corner_radius=8,
            fg_color=colors["accent_primary"],
            hover_color=colors["accent_hover"],
            text_color=colors["text_on_accent"],
            command=self._toggle_demo_feed,
        )
        self._demo_button.grid(row=0, column=1, padx=(0, 8))

        self._autoscale_switch = ctk.CTkSwitch(
            controls,
            text="Auto",
            width=64,
            command=self._on_autoscale_changed,
        )
        self._autoscale_switch.grid(row=0, column=2, padx=(0, 8))
        self._autoscale_switch.select()

        ctk.CTkButton(
            controls,
            text="Clear",
            width=68,
            height=32,
            corner_radius=8,
            fg_color=colors["bg_input"],
            hover_color=colors["sidebar_active"],
            text_color=colors["text_secondary"],
            command=self.clear,
        ).grid(row=0, column=3)

        plot_shell = ctk.CTkFrame(self, fg_color=colors["bg_secondary"], corner_radius=8)
        plot_shell.grid(row=1, column=0, sticky="nsew", padx=24, pady=(16, 24))
        plot_shell.rowconfigure(0, weight=1)
        plot_shell.columnconfigure(0, weight=1)
        self._plot_shell = plot_shell

        self._build_matplotlib_canvas(plot_shell)
        self._update_status()

    def _build_matplotlib_canvas(self, master: ctk.CTkFrame) -> None:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: PLC0415

        colors = theme_manager.get_colors()
        self._figure = Figure(figsize=(7.2, 4.4), dpi=100, facecolor=colors["chart_bg"])
        self._axis = self._figure.add_subplot(111)
        self._apply_axis_theme()

        self._canvas = FigureCanvasTkAgg(self._figure, master=master)
        widget = self._canvas.get_tk_widget()
        widget.configure(background=colors["chart_bg"], highlightthickness=0, borderwidth=0)
        widget.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        self._animation = FuncAnimation(
            self._figure,
            self._animate,
            interval=self._animation_interval_ms,
            blit=False,
            cache_frame_data=False,
        )
        logger.debug("LivePlotPanel FuncAnimation initialized")

    # ------------------------------------------------------------------
    # Animation and data rendering
    # ------------------------------------------------------------------

    def _animate(self, _frame: int) -> tuple[Any, ...]:
        if not self._running:
            return tuple(self._lines.values())
        return self._sync_lines()

    def _sync_lines(self) -> tuple[Any, ...]:
        if not hasattr(self, "_axis"):
            return ()

        for key in self._buffer.series_keys():
            self._ensure_line(key)
            points = self._buffer.points(key)
            if points:
                xs, ys = zip(*points, strict=False)
            else:
                xs, ys = (), ()
            self._lines[key].set_data(xs, ys)

        if self._autoscale:
            self._rescale_axes()
        self._draw_idle()
        return tuple(self._lines.values())

    def _ensure_line(self, key: str) -> None:
        if key in self._lines:
            return

        config = self._ensure_series_config(key)
        line_color = config.color or _default_line_color(len(self._lines))
        (line,) = self._axis.plot(
            [],
            [],
            label=config.label,
            color=line_color,
            linestyle=config.linestyle,
            marker=config.marker,
            linewidth=1.8,
        )
        self._lines[key] = line
        self._axis.legend(loc="upper right")
        logger.debug("LivePlotPanel created line for series=%s", key)

    def _ensure_series_config(self, key: str) -> LiveSeriesConfig:
        normalized = _series_key(key)
        config = self._series_configs.get(normalized)
        if config is None:
            config = LiveSeriesConfig(
                key=normalized,
                label=normalized.replace("_", " ").title(),
            )
            self._series_configs[normalized] = config
        return config

    def _rescale_axes(self) -> None:
        self._axis.relim()
        self._axis.autoscale_view()
        if not any(self._buffer.points(key) for key in self._buffer.series_keys()):
            self._axis.set_xlim(0.0, 10.0)
            self._axis.set_ylim(-1.0, 1.0)

    def _draw_idle(self) -> None:
        if self._canvas is not None:
            self._canvas.draw_idle()

    def _start_animation_event_source(self) -> None:
        if self._animation is not None and self._animation.event_source is not None:
            self._animation.event_source.start()

    def _stop_animation_event_source(self) -> None:
        if self._animation is not None and self._animation.event_source is not None:
            self._animation.event_source.stop()

    # ------------------------------------------------------------------
    # Controls and simulated feed
    # ------------------------------------------------------------------

    def _toggle_running(self) -> None:
        if self._running:
            self.pause()
        else:
            self.resume()

    def _on_autoscale_changed(self) -> None:
        self.set_autoscale(bool(self._autoscale_switch.get()))

    def _toggle_demo_feed(self) -> None:
        if self._demo_running:
            self._stop_demo_feed()
        else:
            self._start_demo_feed()

    def _start_demo_feed(self) -> None:
        self._demo_running = True
        self._demo_start = time.monotonic()
        self._demo_button.configure(text="Stop")
        self.resume()
        self._schedule_demo_tick()
        logger.info("LivePlotPanel simulated feed started")

    def _stop_demo_feed(self) -> None:
        self._demo_running = False
        if self._demo_job is not None:
            try:
                self.after_cancel(self._demo_job)
            except Exception:
                logger.debug("Could not cancel LivePlotPanel demo job", exc_info=True)
            self._demo_job = None
        if hasattr(self, "_demo_button"):
            self._demo_button.configure(text="Simulate")
        self._update_status()
        logger.info("LivePlotPanel simulated feed stopped")

    def _schedule_demo_tick(self) -> None:
        if not self._demo_running:
            return
        self._demo_job = self.after(_DEMO_INTERVAL_MS, self._demo_tick)

    def _demo_tick(self) -> None:
        if not self._demo_running:
            return
        elapsed = time.monotonic() - self._demo_start
        value = math.sin(elapsed * 2.0) + random.uniform(-0.03, 0.03)
        self.append_point(elapsed, value, series="simulated")
        self._update_status()
        self._schedule_demo_tick()

    def _update_status(self) -> None:
        if not hasattr(self, "_status_label"):
            return
        samples = sum(len(self._buffer.points(key)) for key in self._buffer.series_keys())
        state = "running" if self._running else "paused"
        scale = "autoscale" if self._autoscale else "fixed scale"
        feed = "simulated feed on" if self._demo_running else "ready"
        self._status_label.configure(text=f"{state} | {scale} | {samples} samples | {feed}")
        if hasattr(self, "_pause_button"):
            self._pause_button.configure(text="Pause" if self._running else "Resume")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _refresh_theme(self, _mode: str) -> None:
        colors = theme_manager.get_colors()
        self.configure(fg_color=colors["bg_primary"])
        if hasattr(self, "_plot_shell"):
            self._plot_shell.configure(fg_color=colors["bg_secondary"])
        if hasattr(self, "_axis"):
            self._figure.set_facecolor(colors["chart_bg"])
            self._apply_axis_theme()
            self._draw_idle()

    def _apply_axis_theme(self) -> None:
        colors = theme_manager.get_colors()
        self._axis.set_facecolor(colors["chart_bg"])
        self._axis.set_title(self._plot_title, color=colors["text_primary"])
        self._axis.set_xlabel(self._x_label, color=colors["text_secondary"])
        self._axis.set_ylabel(self._y_label, color=colors["text_secondary"])
        self._axis.grid(True, color=colors["chart_grid"], alpha=0.65)
        self._axis.tick_params(colors=colors["text_secondary"])
        for spine in self._axis.spines.values():
            spine.set_color(colors["border"])


def _series_config_map(
    series: Iterable[LiveSeriesConfig] | None,
) -> dict[str, LiveSeriesConfig]:
    configs = {
        _DEFAULT_SERIES: LiveSeriesConfig(
            key=_DEFAULT_SERIES,
            label="Measurement",
        )
    }
    for config in series or ():
        configs[_series_key(config.key)] = config
    return configs


def _series_key(value: str) -> str:
    stripped = str(value).strip()
    return stripped or _DEFAULT_SERIES


def _finite_float(value: Any, *, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite.")
    return numeric


def _default_line_color(index: int) -> str:
    colors = theme_manager.get_colors()
    palette = (
        colors["accent_primary"],
        colors["success"],
        colors["warning"],
        colors["accent_secondary"],
        colors["error"],
        colors["text_secondary"],
    )
    return palette[index % len(palette)]


__all__ = [
    "LivePlotBuffer",
    "LivePlotPanel",
    "LiveSeriesConfig",
    "logger",
]
