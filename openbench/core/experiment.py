"""Experiment base classes for reusable measurement workflows."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)


class ExperimentState(StrEnum):
    """Lifecycle state of an OpenBench experiment run.

    States progress linearly through IDLE → SETUP → RUNNING → TEARDOWN →
    COMPLETED (or FAILED). ABORTED is entered when ``abort()`` is honoured
    inside ``_run()``.
    """

    IDLE = "idle"
    SETUP = "setup"
    RUNNING = "running"
    TEARDOWN = "teardown"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class ExperimentResult:
    """Structured result returned by an OpenBench experiment.

    Attributes:
        name: Human-readable experiment name matching the experiment's ``name``
            field.
        data: Measurement payload produced by ``_run()``.
        metadata: Additional run metadata (timestamps, config snapshots, etc.).
        state: Lifecycle state at the time the result was captured.
        duration_s: Wall-clock duration of the full lifecycle in seconds.
        error: Exception that caused a ``FAILED`` or ``ABORTED`` result, or
            ``None`` on success.
    """

    name: str
    data: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    state: ExperimentState = ExperimentState.COMPLETED
    duration_s: float = 0.0
    error: Exception | None = None


@dataclass
class BaseExperiment(ABC):
    """Abstract base class for OpenBench measurement experiments.

    Subclasses implement ``_run()`` and optionally override the lifecycle hooks
    ``validate()``, ``setup()``, and ``teardown()``. The public ``run()`` method
    coordinates the full lifecycle, ensures ``teardown()`` is always called, and
    returns an ``ExperimentResult`` regardless of success or failure.

    Lifecycle order::

        validate() → setup() → _run() → teardown()

    ``teardown()`` is called even when ``_run()`` raises. The returned
    ``ExperimentResult.state`` reflects the final outcome.

    Simulation mode:
        When ``simulate`` is True (or when ``run(simulate=True)`` is called),
        ``_run()`` should return synthetic data without hardware access. The
        ``_simulate`` property exposes the effective flag inside the lifecycle.

    Progress reporting:
        Call ``report_progress(message, fraction)`` from ``_run()`` to drive
        GUI progress bars or console feedback. ``fraction`` is a float in
        ``[0.0, 1.0]``.

    Abort:
        Call ``abort()`` externally (e.g. from a GUI button handler). Check
        ``_abort_requested`` inside ``_run()`` and honour it by raising
        ``RuntimeError`` or returning partial data early.

    Attributes:
        name: Human-readable experiment identifier.
        simulate: Default simulation flag. Overridable per-run via
            ``run(simulate=...)``.
        on_progress: Optional callback invoked by ``report_progress()``.
            Signature: ``(message: str, fraction: float) -> None``.
    """

    name: str
    simulate: bool = False
    on_progress: Callable[[str, float], None] | None = field(
        default=None, repr=False
    )

    _state: ExperimentState = field(
        default=ExperimentState.IDLE, init=False, repr=False
    )
    _abort_requested: bool = field(default=False, init=False, repr=False)
    _effective_simulate: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Subclass initialization hook called after the dataclass ``__init__``.

        Override in concrete experiments to initialize instance-specific state
        without redefining ``__init__``. The base implementation is a no-op.
        """

    # ------------------------------------------------------------------
    # Public state inspection
    # ------------------------------------------------------------------

    def state(self) -> ExperimentState:
        """Return the current lifecycle state.

        Returns:
            Current experiment state.
        """
        return self._state

    @property
    def _simulate(self) -> bool:
        """Effective simulate flag for use inside lifecycle hooks."""
        return self._effective_simulate

    # ------------------------------------------------------------------
    # Lifecycle hooks — override as needed
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Validate configuration before any hardware interaction.

        Raise ``ValueError`` (or any exception) to abort the run before
        instruments are touched. The default implementation is a no-op.

        Raises:
            ValueError: When the configuration is invalid.
        """

    def setup(self) -> None:
        """Connect and configure instruments for the experiment.

        Called after ``validate()`` and before ``_run()``. The default
        implementation is a no-op; subclasses override this to connect
        instrument adapters and apply initial settings.
        """

    @abstractmethod
    def _run(self) -> dict[str, Any]:
        """Execute the core measurement logic.

        This is the only method subclasses are required to implement. Return a
        dictionary of measurement results. The dictionary becomes
        ``ExperimentResult.data``.

        Check ``self._simulate`` to decide whether to drive real hardware or
        return synthetic data. Check ``self._abort_requested`` periodically and
        raise ``RuntimeError("aborted")`` (or return partial data) to honour an
        abort request.

        Returns:
            Measurement data dictionary that populates ``ExperimentResult.data``.

        Raises:
            Any exception to signal failure; the framework catches it, sets
            ``ExperimentResult.state = FAILED``, and calls ``teardown()``.
        """

    def teardown(self) -> None:
        """Release instruments and clean up after the experiment.

        Always called after ``_run()`` — even when ``_run()`` raises. The
        default implementation is a no-op; subclasses override this to
        disconnect instruments or restore hardware state.
        """

    # ------------------------------------------------------------------
    # Progress and abort control
    # ------------------------------------------------------------------

    def report_progress(self, message: str, fraction: float = 0.0) -> None:
        """Emit a progress update to the registered callback.

        Args:
            message: Human-readable progress description.
            fraction: Completion fraction in ``[0.0, 1.0]``.
        """
        logger.debug("Experiment progress [%s] %.0f%% — %s", self.name, fraction * 100, message)
        if self.on_progress is not None:
            try:
                self.on_progress(message, fraction)
            except Exception:
                logger.warning("Progress callback raised for experiment: %s", self.name, exc_info=True)

    def abort(self) -> None:
        """Request a graceful abort of the running experiment.

        Sets the internal ``_abort_requested`` flag. ``_run()`` implementations
        should poll this flag and stop early when it is True.
        """
        logger.info("Abort requested for experiment: %s", self.name)
        self._abort_requested = True

    # ------------------------------------------------------------------
    # Public run entrypoint
    # ------------------------------------------------------------------

    def run(self, *, simulate: bool | None = None) -> ExperimentResult:
        """Execute the full experiment lifecycle and return a structured result.

        Coordinates ``validate() → setup() → _run() → teardown()`` with state
        tracking, timing, and error capture. ``teardown()`` is guaranteed to
        run even when earlier phases raise.

        The result ``state`` is ``COMPLETED`` on success, ``FAILED`` when any
        phase raises, and ``ABORTED`` when ``_run()`` raises after ``abort()``
        was called.

        Args:
            simulate: Override the instance-level ``simulate`` flag for this
                run. When ``None``, the instance ``simulate`` attribute is used.

        Returns:
            Experiment result containing data, state, timing, and any error.
        """
        self._effective_simulate = simulate if simulate is not None else self.simulate
        self._abort_requested = False
        self._state = ExperimentState.IDLE

        t0 = time.monotonic()
        data: dict[str, Any] = {}
        error: Exception | None = None

        try:
            logger.info(
                "Experiment starting: %s (simulate=%s)", self.name, self._effective_simulate
            )
            self.validate()

            self._state = ExperimentState.SETUP
            logger.debug("Experiment setup: %s", self.name)
            self.setup()

            self._state = ExperimentState.RUNNING
            logger.debug("Experiment running: %s", self.name)
            data = self._run()

            self._state = ExperimentState.COMPLETED
            logger.info("Experiment completed: %s", self.name)

        except Exception as exc:
            error = exc
            if self._abort_requested:
                self._state = ExperimentState.ABORTED
                logger.warning("Experiment aborted: %s — %s", self.name, exc)
            else:
                self._state = ExperimentState.FAILED
                logger.exception("Experiment failed: %s", self.name)

        finally:
            prev_state = self._state
            self._state = ExperimentState.TEARDOWN
            logger.debug("Experiment teardown: %s", self.name)
            try:
                self.teardown()
            except Exception as td_exc:
                logger.exception("Experiment teardown failed: %s", self.name)
                if error is None:
                    error = td_exc
                    prev_state = ExperimentState.FAILED

            self._state = prev_state

        duration_s = time.monotonic() - t0
        logger.info(
            "Experiment finished: %s state=%s duration=%.3fs",
            self.name,
            self._state,
            duration_s,
        )

        return ExperimentResult(
            name=self.name,
            data=data,
            state=self._state,
            duration_s=duration_s,
            error=error,
        )

    # ------------------------------------------------------------------
    # Context manager — setup / teardown without a full run
    # ------------------------------------------------------------------

    def __enter__(self) -> BaseExperiment:
        """Run setup and return the experiment for use in a ``with`` block.

        Returns:
            This experiment instance after ``setup()`` completes.
        """
        self._effective_simulate = self.simulate
        self._state = ExperimentState.SETUP
        self.setup()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Run teardown when leaving a ``with`` block.

        Args:
            exc_type: Exception type raised inside the block, when present.
            exc_value: Exception raised inside the block, when present.
            traceback: Traceback from the exception inside the block.
        """
        self._state = ExperimentState.TEARDOWN
        try:
            self.teardown()
        finally:
            self._state = (
                ExperimentState.FAILED if exc_type is not None else ExperimentState.COMPLETED
            )


__all__ = [
    "BaseExperiment",
    "Experiment",
    "ExperimentResult",
    "ExperimentState",
]


# ---------------------------------------------------------------------------
# Backward-compatible lightweight protocol (kept for duck-typing)
# ---------------------------------------------------------------------------

from typing import Protocol  # noqa: E402


class Experiment(Protocol):
    """Structural protocol for executable OpenBench experiments.

    Use ``BaseExperiment`` when you want the full lifecycle scaffolding.
    This protocol remains for lightweight duck-typing against third-party
    or legacy experiment objects that only implement ``name`` and ``run()``.
    """

    name: str

    def run(self, *, simulate: bool = True) -> ExperimentResult:
        """Execute the experiment.

        Args:
            simulate: Run without physical hardware when True.

        Returns:
            Structured experiment result.
        """
