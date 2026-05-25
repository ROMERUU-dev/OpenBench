"""Re-exports of SOFIA filter topology models for use within OpenBench.

All domain types (enumerations, specs, results) originate in
``sofia_filter_studio.models``. This module surfaces them under the
``openbench.filters`` namespace so experiment and GUI code never needs to
import from the upstream package directly.
"""

from __future__ import annotations

import logging

from sofia_filter_studio.models import (
    Approximation,
    DesignInputs,
    DesignResult,
    FilterKind,
    FilterSpec,
    OpAmpModel,
    ResistorNetwork,
    ResistorSeries,
    Stage,
    StageRealization,
    Topology,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Approximation",
    "DesignInputs",
    "DesignResult",
    "FilterKind",
    "FilterSpec",
    "OpAmpModel",
    "ResistorNetwork",
    "ResistorSeries",
    "Stage",
    "StageRealization",
    "Topology",
]
