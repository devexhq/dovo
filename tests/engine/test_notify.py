"""Contract tests for observer notification dispatch."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition
from dovo.engine.executors.models import ConditionEvaluationResult, StepResult
from dovo.engine.models import RunObserver, RunOutcome
from dovo.engine.notify import safe_notify
from tests.harness.builders import StepBuilder


class _NoOpRunObserver(RunObserver):
    """Test double implementing every RunObserver hook as a no-op; subclass and override only what a test needs."""

    def on_worktree_ready(self, path: Path, active: bool) -> None:
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

    def on_worktree_cleanup(
        self, kept: bool, path: Path, worktree_id: str | None = None, branch: str | None = None
    ) -> None:
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
    def test_none_observer_is_noop(self) -> None:
        step = StepBuilder.command("echo test").build()

        result = safe_notify(None, "on_step_start", 1, 2, step)

        assert result is None

    def test_observer_missing_method_is_noop(self) -> None:
        observer = _NoOpRunObserver()

        result = safe_notify(observer, "on_some_hook_not_in_the_protocol")

        assert result is None

    def test_observer_method_exception_is_swallowed(self) -> None:
        observer = _ExplodingOnStepStartObserver()
        step = StepBuilder.command("echo test").build()

        result = safe_notify(observer, "on_step_start", 1, 2, step)

        assert result is None

    def test_observer_method_called_with_exact_args_and_kwargs(self) -> None:
        observer = _RecordingOnStepStartObserver()
        step = StepBuilder.command("echo test").build()

        safe_notify(observer, "on_step_start", 1, 2, step, stream="stdout")

        assert observer.calls == [((1, 2, step), {"stream": "stdout"})]
