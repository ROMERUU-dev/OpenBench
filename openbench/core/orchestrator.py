"""Instrument discovery and coordination primitives."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openbench.core.interfaces import IInstrument

logger = logging.getLogger(__name__)


@dataclass
class InstrumentOrchestrator:
    """Registry and coordinator for instrument adapters."""

    instruments: dict[str, IInstrument] = field(default_factory=dict)

    def register(self, instrument: IInstrument) -> None:
        """Register an instrument adapter by its public name.

        Args:
            instrument: Adapter implementing the base instrument protocol.
        """

        logger.debug("Registering instrument adapter: %s", instrument.name)
        self.instruments[instrument.name] = instrument

    def get(self, name: str) -> IInstrument | None:
        """Return a registered instrument by name.

        Args:
            name: Instrument adapter name.

        Returns:
            Instrument adapter when registered, otherwise None.
        """

        return self.instruments.get(name)
