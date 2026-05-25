"""SOFIA filter design and validation integration.

Public API surface for ``openbench.filters``:

* **Topology models** (re-exported from SOFIA):
  ``FilterKind``, ``Approximation``, ``Topology``, ``OpAmpModel``,
  ``ResistorSeries``, ``FilterSpec``, ``DesignInputs``, ``DesignResult``.

* **Design**:
  ``FilterDesigner``, ``design_filter``, ``MeasurementSetup``.

* **Validation**:
  ``FilterValidator``, ``ValidationResult``.
"""

from __future__ import annotations

import logging

from .design import FilterDesigner, MeasurementSetup, design_filter
from .topologies import (
    Approximation,
    DesignInputs,
    DesignResult,
    FilterKind,
    FilterSpec,
    OpAmpModel,
    ResistorSeries,
    Topology,
)
from .validation import FilterValidator, ValidationResult

logger = logging.getLogger(__name__)

__all__ = [
    # topology models
    "Approximation",
    "DesignInputs",
    "DesignResult",
    "FilterKind",
    "FilterSpec",
    "OpAmpModel",
    "ResistorSeries",
    "Topology",
    # design
    "FilterDesigner",
    "MeasurementSetup",
    "design_filter",
    # validation
    "FilterValidator",
    "ValidationResult",
]
