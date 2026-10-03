"""Contract tests for observer notification dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from tests.harness.builders import StepBuilder
from worktree.core.catalog.definitions import LoopStepBlock, StepDefinition
from worktree.engine.executors.models import ConditionEvaluationResult, StepResult
from worktree.engine.models import RunObserver, RunOutcome
from worktree.engine.notify import safe_notify


class _NoOpRunObserver(RunObserver):
    """Test double implementing every RunObserver hook as a no-op; subclass and override only what a test needs."""

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        pass

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        pass

    def on_step_output(self, idx: int, total: int, step: StepDefinition, line: str, stream: str = "stdout") -> None:
        pass

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        pass

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        pass

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        pass

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        pass

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        pass

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        pass

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        pass

    def on_run_completed(self, outcome: RunOutcome) -> None:
        pass


class _ExplodingOnStepStartObserver(_NoOpRunObserver):
    """Test double raising from on_step_start."""

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        raise RuntimeError("step start exploded")


class _RecordingOnStepStartObserver(_NoOpRunObserver):
    """Test double recording the exact args and kwargs on_step_start was invoked with."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def on_step_start(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


class SafeNotifyTests:
    """[tier-1/unit] safe_notify: dispatches observer hooks and swallows every failure."""

    def test_none_observer_is_noop(self) -> None:
        """[tier-1/unit] safe_notify: observer=None returns None without raising."""
        step = StepBuilder.command("echo test").build()

        result = safe_notify(None, "on_step_start", 1, 2, step)

        assert result is None

    def test_observer_missing_method_is_noop(self) -> None:
        """[tier-1/unit] safe_notify: an observer lacking method_name returns None without raising."""
        observer = _NoOpRunObserver()

        result = safe_notify(observer, "on_some_hook_not_in_the_protocol")

        assert result is None

    def test_observer_method_exception_is_swallowed(self) -> None:
        """[tier-1/unit] safe_notify: an observer whose method raises RuntimeError returns None, exception not propagated."""
        observer = _ExplodingOnStepStartObserver()
        step = StepBuilder.command("echo test").build()

        result = safe_notify(observer, "on_step_start", 1, 2, step)

        assert result is None

    def test_observer_method_called_with_exact_args_and_kwargs(self) -> None:
        """[tier-1/unit] safe_notify: observer.method_name is invoked with the same *args/**kwargs safe_notify received, verified via a recording double."""
        observer = _RecordingOnStepStartObserver()
        step = StepBuilder.command("echo test").build()

        safe_notify(observer, "on_step_start", 1, 2, step, stream="stdout")

        assert observer.calls == [((1, 2, step), {"stream": "stdout"})]
