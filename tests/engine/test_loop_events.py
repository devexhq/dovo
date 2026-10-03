"""Contract tests for LoopEventEmitter: loop run.log lines and observer notifications."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.harness.runs import NoOpRunObserver
from worktree.engine.executors.models import ConditionEvaluationResult
from worktree.engine.loop_events import LoopEventEmitter

_CONDITION = ConditionEvaluationResult(expression="iteration.index >= 3", passed=False)


class _LoopObserver(NoOpRunObserver):
    """RunObserver recording every loop hook as (hook name, positional args, keyword args)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        self.calls.append(("on_loop_start", (loop_id, max_iterations), {}))

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        self.calls.append(("on_loop_iteration_start", (loop_id, iteration, max_iterations), {}))

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        self.calls.append(
            ("on_loop_conditions_evaluated", (loop_id, results, all_passed), {"next_iteration": next_iteration})
        )

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        self.calls.append(("on_loop_done", (loop_id, status, total_iterations), {}))


class LoopEventEmitterTests:
    """[tier-1/integration] LoopEventEmitter: each lifecycle point logs one run.log line and notifies the observer."""

    @pytest.mark.parametrize(
        ("emit", "log_fields", "observer_call"),
        [
            pytest.param(
                lambda emitter: emitter.start(5),
                {"event": "loop_start", "loop_id": "l", "max_iterations": 5},
                ("on_loop_start", ("l", 5), {}),
                id="start",
            ),
            pytest.param(
                lambda emitter: emitter.iteration_start(2, 5),
                {"event": "loop_iteration_start", "loop_id": "l", "iteration": 2, "max_iterations": 5},
                ("on_loop_iteration_start", ("l", 2, 5), {}),
                id="iteration-start",
            ),
            pytest.param(
                lambda emitter: emitter.conditions_evaluated([_CONDITION], False, 3),
                {
                    "event": "loop_conditions_evaluated",
                    "loop_id": "l",
                    "all_passed": False,
                    "next_iteration": 3,
                    "conditions": [_CONDITION.model_dump()],
                },
                ("on_loop_conditions_evaluated", ("l", [_CONDITION], False), {"next_iteration": 3}),
                id="conditions-evaluated",
            ),
            pytest.param(
                lambda emitter: emitter.done("completed", 2),
                {"event": "loop_done", "loop_id": "l", "status": "completed", "iteration": 2},
                ("on_loop_done", ("l", "completed", 2), {}),
                id="done",
            ),
        ],
    )
    def test_each_event_appends_run_log_line_and_notifies_observer(
        self,
        tmp_path: Path,
        emit: Callable[[LoopEventEmitter], None],
        log_fields: dict[str, object],
        observer_call: tuple[str, tuple[object, ...], dict[str, object]],
    ) -> None:
        """[tier-1/integration] LoopEventEmitter: start(5), iteration_start(2, 5), conditions_evaluated([r], False, 3), done("completed", 2) each append one run.log line with event LOOP_START / LOOP_ITERATION_START / LOOP_CONDITIONS_EVALUATED / LOOP_DONE and loop_id, plus max_iterations / iteration / all_passed+next_iteration+conditions / status+iteration, and call the matching on_loop_* hook with the same arguments."""
        observer = _LoopObserver()

        emit(LoopEventEmitter("l", tmp_path, observer))

        lines = (tmp_path / "run.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        logged = json.loads(lines[0])
        assert {key: logged[key] for key in log_fields} == log_fields
        assert observer.calls == [observer_call]

    def test_none_log_dir_and_none_observer_do_nothing(self, tmp_path: Path) -> None:
        """[tier-1/unit] LoopEventEmitter: session_log_dir None and observer None make every method return without writing or raising."""
        emitter = LoopEventEmitter("l", None, None)

        emitter.start(5)
        emitter.iteration_start(1, 5)
        emitter.conditions_evaluated([_CONDITION], False, None)
        emitter.done("failed", 1)

        assert list(tmp_path.iterdir()) == []
