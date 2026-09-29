"""Loop lifecycle events: run.log lines and observer notifications for one loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from worktree.core.engine.models import RunObserver
from worktree.core.engine.notify import safe_notify
from worktree.core.logs import RunLogEvent, RunLogEventType, append_run_log_event
from worktree.core.step.models import ConditionEvaluationResult


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

    def turn_start(self, turn: int, max_iterations: int) -> None:
        """Log LOOP_TURN_START and call on_loop_turn_start(loop_id, turn, max_iterations)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_TURN_START, loop_id=self.loop_id, turn=turn, max_iterations=max_iterations
            ),
        )
        safe_notify(self.observer, "on_loop_turn_start", self.loop_id, turn, max_iterations)

    def conditions_evaluated(
        self, results: list[ConditionEvaluationResult], all_passed: bool, next_turn: int | None
    ) -> None:
        """Log LOOP_CONDITIONS_EVALUATED and call on_loop_conditions_evaluated(loop_id, results, all_passed, next_turn=next_turn)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_CONDITIONS_EVALUATED,
                loop_id=self.loop_id,
                all_passed=all_passed,
                next_turn=next_turn,
                conditions=[result.model_dump() for result in results],
            ),
        )
        safe_notify(
            self.observer, "on_loop_conditions_evaluated", self.loop_id, results, all_passed, next_turn=next_turn
        )

    def done(self, status: str, turns: int) -> None:
        """Log LOOP_DONE and call on_loop_done(loop_id, status, turns)."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(event=RunLogEventType.LOOP_DONE, loop_id=self.loop_id, status=status, turn=turns),
        )
        safe_notify(self.observer, "on_loop_done", self.loop_id, status, turns)
