"""Filter design facade: wraps SOFIA's design engine for use inside OpenBench.

The module delegates all computation to ``sofia_filter_studio`` and adds only
the thin glue needed to tie design outputs into OpenBench's measurement
workflows (frequency sweep suggestions, SPICE netlist export).

Example::

    from openbench.filters.design import FilterDesigner
    from openbench.filters.topologies import (
        Approximation, DesignInputs, FilterKind, FilterSpec, Topology,
    )

    inputs = DesignInputs(
        kind=FilterKind.LOWPASS,
        approximation=Approximation.BUTTERWORTH,
        spec=FilterSpec(passband_hz=1_000.0, stopband_hz=5_000.0),
        passband_ripple_db=1.0,
        stopband_attenuation_db=40.0,
        topology=Topology.SALLEN_KEY,
    )
    designer = FilterDesigner(inputs)
    result = designer.design()
    print(designer.render_netlist(result))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sofia_filter_studio.design import design_filter as _sofia_design
from sofia_filter_studio.design import format_result as _sofia_format_result
from sofia_filter_studio.netlist import render_netlist as _sofia_render_netlist

from .topologies import DesignInputs, DesignResult, FilterKind

logger = logging.getLogger(__name__)


@dataclass
class MeasurementSetup:
    """Suggested frequency sweep parameters derived from a filter design.

    Attributes:
        start_hz: Recommended sweep start frequency in hertz.
        stop_hz: Recommended sweep stop frequency in hertz.
        num_points: Recommended number of logarithmically-spaced points.
        excitation_v: Suggested excitation amplitude for lock-in measurements.
        notes: Human-readable rationale for the parameter choices.
    """

    start_hz: float
    stop_hz: float
    num_points: int
    excitation_v: float
    notes: list[str]


class FilterDesigner:
    """Thin wrapper around SOFIA's ``design_filter`` for OpenBench workflows.

    Args:
        inputs: SOFIA ``DesignInputs`` specification for the desired filter.
    """

    def __init__(self, inputs: DesignInputs) -> None:
        self._inputs = inputs

    def design(self) -> DesignResult:
        """Run SOFIA's filter synthesis and return the full design result.

        Returns:
            Completed ``DesignResult`` containing poles, stage realizations,
            component values, and synthesis warnings.

        Raises:
            ValueError: If ``DesignInputs`` fails SOFIA's validation checks.
        """

        logger.info(
            "Designing %s %s filter (passband=%s Hz)",
            self._inputs.approximation,
            self._inputs.kind,
            self._inputs.spec.passband_hz,
        )
        result = _sofia_design(self._inputs)
        if result.warnings:
            for warning in result.warnings:
                logger.warning("SOFIA: %s", warning)
        logger.info(
            "Filter designed: order=%d, stages=%d",
            result.order,
            len(result.stages),
        )
        return result

    def render_netlist(self, result: DesignResult) -> str:
        """Render a SPICE netlist string for the given design result.

        Args:
            result: ``DesignResult`` previously returned by :meth:`design`.

        Returns:
            Multi-line SPICE netlist string ready for simulation.
        """

        return _sofia_render_netlist(self._inputs, result)

    def format_result(self, result: DesignResult) -> str:
        """Serialise ``result`` to a human-readable JSON string.

        Args:
            result: ``DesignResult`` previously returned by :meth:`design`.

        Returns:
            Indented JSON representation of the design result.
        """

        return _sofia_format_result(result)

    def measurement_setup(self, result: DesignResult) -> MeasurementSetup:
        """Suggest a frequency sweep setup for validating the designed filter.

        The sweep covers three decades around the passband edge so that both
        the passband flatness and the stopband roll-off are clearly captured.

        Args:
            result: ``DesignResult`` previously returned by :meth:`design`.

        Returns:
            ``MeasurementSetup`` with start/stop frequencies and point count
            appropriate for the designed filter.
        """

        passband_hz = _passband_center_hz(self._inputs)
        start_hz = max(1.0, passband_hz / 100.0)
        stop_hz = passband_hz * 100.0
        num_points = 100

        notes: list[str] = [
            f"Sweep spans {start_hz:.1f} Hz – {stop_hz:.1f} Hz (4 decades around passband edge).",
            f"Filter order: {result.order}, topology: {self._inputs.topology}.",
        ]
        if result.warnings:
            notes.append(f"SOFIA warnings: {'; '.join(result.warnings)}")

        return MeasurementSetup(
            start_hz=start_hz,
            stop_hz=stop_hz,
            num_points=num_points,
            excitation_v=0.1,
            notes=notes,
        )


def design_filter(inputs: DesignInputs) -> DesignResult:
    """Convenience function: design a filter from ``inputs``.

    Equivalent to ``FilterDesigner(inputs).design()``.

    Args:
        inputs: SOFIA ``DesignInputs`` specification.

    Returns:
        Completed ``DesignResult``.
    """

    return FilterDesigner(inputs).design()


def _passband_center_hz(inputs: DesignInputs) -> float:
    """Extract a representative passband frequency in hertz."""

    passband = inputs.spec.passband_hz
    if isinstance(passband, tuple):
        low, high = map(float, passband)
        return float(np.sqrt(low * high))
    return float(passband)


__all__ = [
    "FilterDesigner",
    "MeasurementSetup",
    "design_filter",
]
