"""Loop lifecycle events: run.log lines and observer notifications for one loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dovo.core.logs import RunLogEvent, RunLogEventType, append_run_log_event
from dovo.engine.executors.models import ConditionEvaluationResult
from dovo.engine.models import RunObserver
from dovo.engine.notify import safe_notify


@dataclass(frozen=True)
class LoopEventEmitter:
    """Appends loop run.log events and notifies the observer for one loop."""

    loop_id: str
    session_log_dir: Path | None
    observer: RunObserver | None

    def start(self, max_iterations: int) -> None:
        """Log LOOP_START and call on_loop_start(loop_id, max_iterations)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(event=RunLogEventType.LOOP_START, loop_id=self.loop_id, max_iterations=max_iterations),
        )
        safe_notify(self.observer, "on_loop_start", self.loop_id, max_iterations)

    def iteration_start(self, iteration: int, max_iterations: int) -> None:
        """Log LOOP_ITERATION_START and call on_loop_iteration_start(loop_id, iteration, max_iterations)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_ITERATION_START,
                loop_id=self.loop_id,
                iteration=iteration,
                max_iterations=max_iterations,
            ),
        )
        safe_notify(self.observer, "on_loop_iteration_start", self.loop_id, iteration, max_iterations)

    def conditions_evaluated(
        self, results: list[ConditionEvaluationResult], all_passed: bool, next_iteration: int | None
    ) -> None:
        """Log LOOP_CONDITIONS_EVALUATED and call on_loop_conditions_evaluated(loop_id, results, all_passed, next_iteration=next_iteration)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_CONDITIONS_EVALUATED,
                loop_id=self.loop_id,
                all_passed=all_passed,
                next_iteration=next_iteration,
                conditions=[result.model_dump() for result in results],
            ),
        )
        safe_notify(
            self.observer,
            "on_loop_conditions_evaluated",
            self.loop_id,
            results,
            all_passed,
            next_iteration=next_iteration,
        )

    def done(self, status: str, total_iterations: int) -> None:
        """Log LOOP_DONE with iteration=total_iterations and call on_loop_done(loop_id, status, total_iterations)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_DONE, loop_id=self.loop_id, status=status, iteration=total_iterations
            ),
        )
        safe_notify(self.observer, "on_loop_done", self.loop_id, status, total_iterations)
