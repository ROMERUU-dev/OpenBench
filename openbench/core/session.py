"""Measurement session lifecycle management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class MeasurementSession:
    """Metadata container for a measurement session."""

    name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, str] = field(default_factory=dict)

    def add_metadata(self, key: str, value: str) -> None:
        """Attach metadata to the session.

        Args:
            key: Metadata key.
            value: Metadata value.
        """

        logger.debug("Adding session metadata: %s", key)
        self.metadata[key] = value
