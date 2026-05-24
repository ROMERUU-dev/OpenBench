"""Experiment base classes for reusable measurement workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ExperimentResult:
    """Structured result returned by an OpenBench experiment.

    Attributes:
        name: Human-readable experiment name.
        data: Measurement payload produced by the experiment.
        metadata: Additional run metadata.
    """

    name: str
    data: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Experiment(Protocol):
    """Protocol for executable OpenBench experiments."""

    name: str

    def run(self, *, simulate: bool = True) -> ExperimentResult:
        """Execute the experiment.

        Args:
            simulate: Run without physical hardware when true.

        Returns:
            Structured experiment result.
        """
