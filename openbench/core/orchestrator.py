"""Instrument discovery and coordination primitives."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import TracebackType
from typing import TypeVar

from openbench.core.interfaces import IInstrument, InstrumentStatus

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=IInstrument)


@dataclass
class InstrumentOrchestrator:
    """Registry and coordinator for OpenBench instrument adapters.

    The orchestrator owns the central instrument registry and provides lifecycle
    management (bulk connect / disconnect), typed queries so experiments can
    locate instruments by their abstract interface class, and optional VISA
    resource discovery for hosts with pyvisa installed.

    When ``simulate`` is True every adapter registered via ``register`` has its
    own ``simulate`` flag forced to True before being added to the registry.
    This lets experiments run end-to-end without hardware during CI or
    development without changing individual adapter configuration.

    Attributes:
        simulate: When True, force simulation mode on all registered adapters.
        instruments: Public mapping from instrument name to registered adapter.
            Read-only iteration is safe; mutate via ``register`` / ``unregister``
            so lifecycle invariants are maintained.
    """

    simulate: bool = False
    instruments: dict[str, IInstrument] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register(self, instrument: IInstrument) -> None:
        """Register an instrument adapter under its ``name``.

        If the orchestrator is in simulation mode and the adapter's own
        ``simulate`` flag is False, it is set to True before registration so
        the instrument behaves consistently with the orchestrator-level flag.

        Args:
            instrument: Adapter implementing the base ``IInstrument`` protocol.

        Raises:
            ValueError: If an adapter with the same name is already registered.
        """
        if instrument.name in self.instruments:
            raise ValueError(
                f"Instrument already registered: {instrument.name!r}. "
                "Unregister it first or use a unique name."
            )

        if self.simulate and not instrument.simulate:
            instrument.simulate = True
            logger.debug(
                "Orchestrator simulation mode applied to instrument: %s", instrument.name
            )

        logger.debug("Registering instrument adapter: %s", instrument.name)
        self.instruments[instrument.name] = instrument

    def unregister(self, name: str) -> IInstrument | None:
        """Remove and return a registered adapter by name.

        The adapter is not disconnected; callers are responsible for lifecycle
        cleanup before unregistering.

        Args:
            name: Instrument adapter name.

        Returns:
            Removed adapter when found, otherwise ``None``.
        """
        instrument = self.instruments.pop(name, None)
        if instrument is not None:
            logger.debug("Unregistered instrument adapter: %s", name)
        return instrument

    def get(self, name: str) -> IInstrument | None:
        """Return a registered adapter by name.

        Args:
            name: Instrument adapter name.

        Returns:
            Registered adapter when found, otherwise ``None``.
        """
        return self.instruments.get(name)

    def get_by_interface(self, interface_type: type[T]) -> list[T]:
        """Return all registered adapters that implement ``interface_type``.

        Experiments use this to locate instruments by their abstract role
        (e.g. ``IDCSupply``, ``IOscilloscope``) without depending on concrete
        backend classes.

        Args:
            interface_type: Abstract interface class to filter by.

        Returns:
            Adapters that are instances of ``interface_type``, possibly empty.
        """
        return [
            instrument  # type: ignore[return-value]
            for instrument in self.instruments.values()
            if isinstance(instrument, interface_type)
        ]

    def list_instruments(self) -> list[tuple[str, InstrumentStatus]]:
        """Return names and connection statuses of all registered adapters.

        Returns:
            List of ``(name, status)`` pairs sorted alphabetically by name.
        """
        return [
            (name, instrument.status())
            for name, instrument in sorted(self.instruments.items())
        ]

    # ------------------------------------------------------------------
    # Bulk lifecycle
    # ------------------------------------------------------------------

    def connect_all(self) -> dict[str, Exception | None]:
        """Connect all registered adapters in registration order.

        Individual connect failures are caught, logged, and returned in the
        result mapping so the orchestrator continues connecting remaining
        instruments even when one adapter fails.

        Returns:
            Mapping from instrument name to the ``Exception`` raised, or ``None``
            when the adapter connected (or was already connected) successfully.
        """
        results: dict[str, Exception | None] = {}

        for name, instrument in self.instruments.items():
            try:
                instrument.connect()
                results[name] = None
                logger.info("Instrument connected: %s", name)
            except Exception as exc:
                results[name] = exc
                logger.warning(
                    "Instrument connect failed (continuing): %s — %s", name, exc
                )

        return results

    def disconnect_all(self) -> dict[str, Exception | None]:
        """Disconnect all registered adapters in registration order.

        Individual disconnect failures are caught, logged, and returned so all
        instruments are attempted even when one adapter raises.

        Returns:
            Mapping from instrument name to the ``Exception`` raised, or ``None``
            when the adapter disconnected successfully.
        """
        results: dict[str, Exception | None] = {}

        for name, instrument in self.instruments.items():
            try:
                instrument.disconnect()
                results[name] = None
                logger.info("Instrument disconnected: %s", name)
            except Exception as exc:
                results[name] = exc
                logger.warning(
                    "Instrument disconnect failed (continuing): %s — %s", name, exc
                )

        return results

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> InstrumentOrchestrator:
        """Connect all registered instruments and return the orchestrator.

        Returns:
            This orchestrator instance after connecting all adapters.
        """
        self.connect_all()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Disconnect all registered instruments on context manager exit.

        Args:
            exc_type: Exception type raised inside the ``with`` block.
            exc_value: Exception instance raised inside the ``with`` block.
            traceback: Traceback from the exception raised inside the ``with`` block.
        """
        self.disconnect_all()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def discover_visa(*, simulate: bool = False) -> list[str]:
        """Probe available VISA resources on the local system via pyvisa.

        Returns the raw VISA resource strings (e.g. ``"USB0::0x2A8D::...::INSTR"``)
        that pyvisa found.  Callers decide which backend adapter to instantiate
        for each resource.  When pyvisa is not installed, or no ResourceManager
        can be opened, the method logs a warning and returns an empty list
        instead of raising so that code paths without hardware degrade
        gracefully.

        Args:
            simulate: Skip hardware probing and return an empty list immediately.

        Returns:
            Ordered list of VISA resource strings discovered on this host.
        """
        if simulate:
            logger.debug("VISA discovery skipped — simulation mode active")
            return []

        try:
            import pyvisa  # type: ignore[import-untyped]

            rm = pyvisa.ResourceManager()
            resources = list(rm.list_resources())
            logger.info(
                "VISA discovery found %d resource(s): %s", len(resources), resources
            )
            return resources
        except Exception as exc:
            logger.warning("VISA discovery unavailable: %s", exc)
            return []


__all__ = ["InstrumentOrchestrator"]
