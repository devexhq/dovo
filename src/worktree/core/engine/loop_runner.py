"""Sequential turn execution runner for declarative LoopStepBlock workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from worktree.common.models import FailurePolicy
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.engine.models import (
    FailurePrompter,
    LoopPromptDecision,
    RunObserver,
    StepLoopState,
)
from worktree.core.engine.notify import safe_notify
from worktree.core.engine.step_executor import StepCoordinator
from worktree.core.logs import RunLogEvent, RunLogEventType, append_run_log_event
from worktree.core.step.models import (
    ConditionEvaluationResult,
    ExecutionIdentity,
    LoopStepBlock,
    PreviousStepMetadata,
    StepDefinition,
    StepResult,
)
from worktree.core.step.services.conditions import evaluate_condition
from worktree.core.step.services.metadata import previous_step_metadata_from_result


class LoopBlockRunner:
    """Sequential turn execution runner for a LoopStepBlock."""

    def __init__(
        self,
        loop: LoopStepBlock,
        sandbox_path: Path,
        *,
        coordinator: StepCoordinator,
        context: dict[str, Any] | None = None,
        on_output: Callable[[str, str], None] | None = None,
        observer: RunObserver | None = None,
        failure_prompter: FailurePrompter | None = None,
        no_tty: bool = False,
        step_index: int = 1,
        identity: ExecutionIdentity | None = None,
        session_tmp_dir: Path | None = None,
        session_log_dir: Path | None = None,
        save_attempt_logs: bool = True,
        session_id: str | None = None,
        artifacts_dir: Path | None = None,
        artifacts_db: ArtifactsRepository | None = None,
    ) -> None:
        self.loop = loop
        self.sandbox_path = sandbox_path.resolve()
        self.coordinator = coordinator
        self.context = context or {}
        self.on_output = on_output
        self.observer = observer
        self.failure_prompter = failure_prompter
        self.no_tty = no_tty
        self.step_index = step_index
        self.identity = identity
        self.session_tmp_dir = session_tmp_dir
        self.session_log_dir = session_log_dir
        self.save_attempt_logs = save_attempt_logs
        self.session_id = session_id
        self.artifacts_dir = artifacts_dir
        self.artifacts_db = artifacts_db

    def _notify_start(self, max_iterations: int) -> None:
        """Notify observer that loop execution has started."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(event=RunLogEventType.LOOP_START, loop_id=self.loop.id, max_iterations=max_iterations),
        )
        safe_notify(self.observer, "on_loop_start", self.loop.id, max_iterations)

    def _notify_turn(self, turn: int, max_iterations: int) -> None:
        """Notify observer that a loop turn is beginning."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_TURN_START, loop_id=self.loop.id, turn=turn, max_iterations=max_iterations
            ),
        )
        safe_notify(self.observer, "on_loop_turn_start", self.loop.id, turn, max_iterations)

    def _notify_done(self, status: str, turns: int) -> None:
        """Notify observer that loop execution has finished."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(event=RunLogEventType.LOOP_DONE, loop_id=self.loop.id, status=status, turn=turns),
        )
        safe_notify(self.observer, "on_loop_done", self.loop.id, status, turns)

    def _notify_conditions(
        self,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_turn: int | None,
    ) -> None:
        """Notify observer of evaluated loop until conditions."""
        append_run_log_event(
            self.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.LOOP_CONDITIONS_EVALUATED,
                loop_id=self.loop.id,
                all_passed=all_passed,
                next_turn=next_turn,
                conditions=[r.model_dump() for r in results],
            ),
        )
        safe_notify(
            self.observer, "on_loop_conditions_evaluated", self.loop.id, results, all_passed, next_turn=next_turn
        )

    def _build_step_context(self, turn: int) -> dict[str, Any]:
        """Construct execution context dictionary for current loop turn."""
        step_context = dict(self.context)
        step_context["iteration_index"] = turn
        return step_context

    def _execute_one_sub_step(
        self,
        sub_idx: int,
        sub_step: StepDefinition,
        turn: int,
        state: StepLoopState,
        historical: list[PreviousStepMetadata],
        turn_results: list[StepResult],
        turn_map: dict[str, StepResult],
    ) -> tuple[str, str | None]:
        """Execute one loop sub-step, recording its result into the turn's accumulators."""
        previous_step = historical[-1] if historical else PreviousStepMetadata()
        action, result, error = self.coordinator.execute_one_step(
            state,
            sub_step,
            idx=sub_idx,
            total=len(self.loop.do),
            step_context=self._build_step_context(turn),
            previous_step=previous_step,
            steps=historical,
            loop_iteration=turn,
        )
        if result is not None:
            turn_results.append(result)
            turn_map[sub_step.id] = result
            historical.append(previous_step_metadata_from_result(result, step_index=len(historical) + 1))
        return action, error

    def _execute_turn(
        self,
        turn: int,
        state: StepLoopState,
        accumulated: Sequence[StepResult],
    ) -> tuple[str, list[StepResult], dict[str, StepResult], str | None]:
        """Execute all sub-steps in one turn of the loop."""
        turn_results: list[StepResult] = []
        turn_map: dict[str, StepResult] = {}
        historical: list[PreviousStepMetadata] = [
            previous_step_metadata_from_result(r, step_index=i + 1) for i, r in enumerate(accumulated)
        ]
        for sub_idx, sub_step in enumerate(self.loop.do, start=1):
            action, error = self._execute_one_sub_step(
                sub_idx, sub_step, turn, state, historical, turn_results, turn_map
            )
            if action == "abort":
                return "abort", turn_results, turn_map, error
        return "ok", turn_results, turn_map, None

    def _evaluate_until_conditions(
        self,
        turn: int,
        turn_map: dict[str, StepResult],
    ) -> tuple[bool, list[ConditionEvaluationResult]]:
        """Evaluate loop until conditions against turn results."""
        results = [evaluate_condition(expr, iteration_index=turn, step_results=turn_map) for expr in self.loop.until]
        all_passed = all(r.passed for r in results)
        return all_passed, results

    def _prompt_max_iterations_decision(
        self,
        turn: int,
        max_iterations: int,
    ) -> tuple[str, int, str | None]:
        """Prompt user when max iterations is reached."""
        if self.no_tty or self.failure_prompter is None:
            msg = f"Loop '{self.loop.id}' reached max_iterations ({max_iterations}) and run is non-interactive."
            return LoopPromptDecision.ABORT, max_iterations, msg

        decision = self.failure_prompter.prompt_loop_max_iterations(
            loop=self.loop,
            iteration=turn,
            diagnostic=f"Reached max_iterations ({max_iterations}) without meeting 'until' conditions.",
            grant_count=3,
        )
        if decision == LoopPromptDecision.GRANT:
            return LoopPromptDecision.GRANT, max_iterations + 3, None
        if decision == LoopPromptDecision.CONTINUE:
            return LoopPromptDecision.CONTINUE, max_iterations, None
        return LoopPromptDecision.ABORT, max_iterations, f"Loop '{self.loop.id}' aborted by user after max_iterations."

    def _handle_max_iterations(
        self,
        turn: int,
        max_iterations: int,
    ) -> tuple[str, int, str | None]:
        """Handle max iterations reached according to loop policy or user prompt."""
        policy = self.loop.on_max_iterations
        if policy == FailurePolicy.ABORT:
            err = f"Loop '{self.loop.id}' reached max_iterations ({max_iterations}) without meeting 'until' conditions."
            return LoopPromptDecision.ABORT, max_iterations, err
        if policy == FailurePolicy.CONTINUE:
            return LoopPromptDecision.CONTINUE, max_iterations, None
        return self._prompt_max_iterations_decision(turn, max_iterations)

    def _run_turn_cycle(
        self,
        turn: int,
        max_iterations: int,
        state: StepLoopState,
        accumulated: Sequence[StepResult],
    ) -> tuple[str, list[StepResult], bool, str | None]:
        """Execute turn sub-steps and evaluate until condition status."""
        self._notify_turn(turn, max_iterations)
        status, turn_results, turn_map, error = self._execute_turn(turn, state, accumulated)
        if status == LoopPromptDecision.ABORT:
            self._notify_done("failed", turn)
            return LoopPromptDecision.ABORT, turn_results, False, error

        all_passed, condition_results = self._evaluate_until_conditions(turn, turn_map)
        next_turn = turn + 1 if (not all_passed and turn < max_iterations) else None
        self._notify_conditions(condition_results, all_passed, next_turn)
        if all_passed:
            self._notify_done("completed", turn)
            return LoopPromptDecision.CONTINUE, turn_results, True, None
        return LoopPromptDecision.CONTINUE, turn_results, False, None

    def _process_max_iteration_ceiling(
        self,
        turn: int,
        max_iterations: int,
        state: StepLoopState,
    ) -> tuple[str, int, str | None]:
        """Handle reached iteration ceiling by granting more turns, continuing, or aborting."""
        action, new_max, max_error = self._handle_max_iterations(turn, max_iterations)
        if action == LoopPromptDecision.GRANT:
            return LoopPromptDecision.GRANT, new_max, None
        if action == LoopPromptDecision.CONTINUE:
            warning = f"Loop '{self.loop.id}' reached max_iterations ({max_iterations}) without meeting 'until' conditions; continuing."
            state.warnings.append(warning)
            self._notify_done("completed", turn)
            return LoopPromptDecision.CONTINUE, max_iterations, None
        self._notify_done("failed", turn)
        return LoopPromptDecision.ABORT, max_iterations, max_error

    def run(self, state: StepLoopState) -> tuple[str, list[StepResult], str | None]:
        """Execute all turns of the loop block until until conditions pass or ceiling is hit."""
        max_iterations = self.loop.max_iterations
        turn = 1
        accumulated: list[StepResult] = []
        self._notify_start(max_iterations)

        while turn <= max_iterations:
            status, turn_results, passed, error = self._run_turn_cycle(
                turn, max_iterations, state, [*state.step_results, *accumulated]
            )
            accumulated.extend(turn_results)
            if status == LoopPromptDecision.ABORT or passed:
                return status, accumulated, error

            if turn < max_iterations:
                turn += 1
                continue

            action, new_max, max_error = self._process_max_iteration_ceiling(turn, max_iterations, state)
            if action == LoopPromptDecision.GRANT:
                max_iterations = new_max
                turn += 1
                continue
            return action, accumulated, max_error

        return LoopPromptDecision.CONTINUE, accumulated, None
