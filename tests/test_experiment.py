"""Tests for BaseExperiment lifecycle hooks and state transitions."""

from __future__ import annotations

import pytest

from openbench.core.experiment import BaseExperiment, ExperimentResult, ExperimentState


# ---------------------------------------------------------------------------
# Concrete experiment stubs
# ---------------------------------------------------------------------------


class MinimalExperiment(BaseExperiment):
    """Simplest valid experiment — returns a fixed data dict."""

    def _run(self) -> dict:
        return {"value": 42}


class HookedExperiment(BaseExperiment):
    """Records which hooks were called and in what order."""

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    def validate(self) -> None:
        self.calls.append("validate")

    def setup(self) -> None:
        self.calls.append("setup")

    def _run(self) -> dict:
        self.calls.append("run")
        return {"done": True}

    def teardown(self) -> None:
        self.calls.append("teardown")


class FailingRunExperiment(BaseExperiment):
    """_run() raises; teardown must still be called."""

    def __post_init__(self) -> None:
        self.teardown_called = False

    def _run(self) -> dict:
        raise RuntimeError("hardware failure")

    def teardown(self) -> None:
        self.teardown_called = True


class FailingSetupExperiment(BaseExperiment):
    """setup() raises; teardown must still be called."""

    def __post_init__(self) -> None:
        self.teardown_called = False

    def setup(self) -> None:
        raise RuntimeError("setup failure")

    def _run(self) -> dict:
        return {}

    def teardown(self) -> None:
        self.teardown_called = True


class FailingValidateExperiment(BaseExperiment):
    """validate() raises; setup and teardown must still be handled."""

    def __post_init__(self) -> None:
        self.setup_called = False
        self.teardown_called = False

    def validate(self) -> None:
        raise ValueError("bad config")

    def setup(self) -> None:
        self.setup_called = True

    def _run(self) -> dict:
        return {}

    def teardown(self) -> None:
        self.teardown_called = True


class AbortableExperiment(BaseExperiment):
    """Calls abort() from inside _run() to simulate an in-flight cancellation."""

    def _run(self) -> dict:
        self.abort()  # simulates external abort arriving mid-run
        raise RuntimeError("aborted")


class ProgressExperiment(BaseExperiment):
    """Emits progress events during _run()."""

    def _run(self) -> dict:
        self.report_progress("step 1", 0.25)
        self.report_progress("step 2", 0.75)
        self.report_progress("done", 1.0)
        return {}


class SimulationAwareExperiment(BaseExperiment):
    """Returns different data depending on simulate flag."""

    def _run(self) -> dict:
        return {"simulated": self._simulate}


class ContextManagerExperiment(BaseExperiment):
    """Tracks setup/teardown calls for context-manager tests."""

    def __post_init__(self) -> None:
        self.setup_calls = 0
        self.teardown_calls = 0

    def setup(self) -> None:
        self.setup_calls += 1

    def _run(self) -> dict:
        return {}

    def teardown(self) -> None:
        self.teardown_calls += 1


# ---------------------------------------------------------------------------
# Lifecycle order
# ---------------------------------------------------------------------------


def test_lifecycle_hooks_called_in_order() -> None:
    """validate → setup → _run → teardown are called in strict order."""
    exp = HookedExperiment(name="hooked")
    exp.run()
    assert exp.calls == ["validate", "setup", "run", "teardown"]


def test_successful_run_returns_completed_state() -> None:
    """run() returns COMPLETED state on success."""
    result = MinimalExperiment(name="minimal").run()
    assert result.state == ExperimentState.COMPLETED


def test_result_contains_run_data() -> None:
    """ExperimentResult.data reflects what _run() returned."""
    result = MinimalExperiment(name="minimal").run()
    assert result.data == {"value": 42}


def test_result_name_matches_experiment_name() -> None:
    """ExperimentResult.name matches the experiment name field."""
    result = MinimalExperiment(name="my-experiment").run()
    assert result.name == "my-experiment"


def test_result_records_duration() -> None:
    """ExperimentResult.duration_s is a positive number."""
    result = MinimalExperiment(name="minimal").run()
    assert result.duration_s >= 0.0


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_teardown_called_when_run_fails() -> None:
    """teardown() executes even when _run() raises."""
    exp = FailingRunExperiment(name="fail-run")
    result = exp.run()
    assert exp.teardown_called is True
    assert result.state == ExperimentState.FAILED


def test_result_carries_error_on_failure() -> None:
    """ExperimentResult.error holds the exception on FAILED runs."""
    result = FailingRunExperiment(name="fail-run").run()
    assert isinstance(result.error, RuntimeError)
    assert "hardware failure" in str(result.error)


def test_teardown_called_when_setup_fails() -> None:
    """teardown() executes even when setup() raises."""
    exp = FailingSetupExperiment(name="fail-setup")
    result = exp.run()
    assert exp.teardown_called is True
    assert result.state == ExperimentState.FAILED


def test_validate_failure_skips_setup_but_calls_teardown() -> None:
    """When validate() raises, setup() is skipped and teardown() still runs."""
    exp = FailingValidateExperiment(name="bad-config")
    result = exp.run()
    assert exp.setup_called is False
    assert exp.teardown_called is True
    assert result.state == ExperimentState.FAILED
    assert isinstance(result.error, ValueError)


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------


def test_abort_mid_run_sets_aborted_state() -> None:
    """abort() called inside _run() produces ABORTED state."""
    exp = AbortableExperiment(name="abortable")
    result = exp.run()
    assert result.state == ExperimentState.ABORTED


class NonAbortingExperiment(BaseExperiment):
    """Never calls abort(); completes normally."""

    def _run(self) -> dict:
        return {"data": 1}


def test_no_abort_completes_normally() -> None:
    """Without abort(), experiment completes normally."""
    result = NonAbortingExperiment(name="normal").run()
    assert result.state == ExperimentState.COMPLETED


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


def test_progress_callback_receives_events() -> None:
    """on_progress callback receives all report_progress() calls."""
    events: list[tuple[str, float]] = []

    exp = ProgressExperiment(
        name="progress",
        on_progress=lambda msg, frac: events.append((msg, frac)),
    )
    exp.run()

    assert events == [("step 1", 0.25), ("step 2", 0.75), ("done", 1.0)]


def test_no_callback_does_not_raise() -> None:
    """report_progress() is safe when no callback is registered."""
    exp = ProgressExperiment(name="progress")
    result = exp.run()
    assert result.state == ExperimentState.COMPLETED


# ---------------------------------------------------------------------------
# Simulation mode
# ---------------------------------------------------------------------------


def test_simulate_flag_propagated_to_run() -> None:
    """run(simulate=True) sets _simulate inside _run()."""
    result = SimulationAwareExperiment(name="sim", simulate=False).run(simulate=True)
    assert result.data["simulated"] is True


def test_simulate_flag_false_by_default() -> None:
    """_simulate is False when neither instance flag nor run override is set."""
    result = SimulationAwareExperiment(name="sim").run()
    assert result.data["simulated"] is False


def test_run_simulate_overrides_instance_flag() -> None:
    """run(simulate=False) overrides a True instance flag."""
    result = SimulationAwareExperiment(name="sim", simulate=True).run(simulate=False)
    assert result.data["simulated"] is False


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


def test_context_manager_calls_setup_and_teardown() -> None:
    """Using experiment as a context manager calls setup() and teardown()."""
    exp = ContextManagerExperiment(name="cm")
    with exp:
        pass
    assert exp.setup_calls == 1
    assert exp.teardown_calls == 1


def test_context_manager_teardown_called_on_exception() -> None:
    """teardown() is called even when the with-block raises."""
    exp = ContextManagerExperiment(name="cm")
    with pytest.raises(ValueError):
        with exp:
            raise ValueError("error in block")
    assert exp.teardown_calls == 1


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------


def test_state_is_idle_before_run() -> None:
    """state() returns IDLE before run() is called."""
    exp = MinimalExperiment(name="minimal")
    assert exp.state() == ExperimentState.IDLE


def test_state_is_completed_after_successful_run() -> None:
    """state() returns COMPLETED after a successful run()."""
    exp = MinimalExperiment(name="minimal")
    exp.run()
    assert exp.state() == ExperimentState.COMPLETED


def test_state_is_failed_after_failed_run() -> None:
    """state() returns FAILED after a failing run()."""
    exp = FailingRunExperiment(name="fail-run")
    exp.run()
    assert exp.state() == ExperimentState.FAILED


# ---------------------------------------------------------------------------
# ExperimentResult structure
# ---------------------------------------------------------------------------


def test_experiment_result_error_none_on_success() -> None:
    """ExperimentResult.error is None on a successful run."""
    result = MinimalExperiment(name="minimal").run()
    assert result.error is None


def test_experiment_result_is_frozen() -> None:
    """ExperimentResult is immutable after creation."""
    result = MinimalExperiment(name="minimal").run()
    with pytest.raises((AttributeError, TypeError)):
        result.name = "changed"  # type: ignore[misc]
