"""Sequential turn execution runner for declarative LoopStepBlock workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.runtime.artifact_publish import auto_publish_step_artifacts
from worktree.core.runtime.failure import (
    effective_terminal_policy,
    mark_continued_after_prompt,
    step_failure_diagnostic,
)
from worktree.core.runtime.log_writer import append_run_log_event
from worktree.core.runtime.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunCheckpoint,
    RunLogEvent,
    RunLogEventType,
    RunObserver,
    RunPauseStore,
    StepLoopState,
)
from worktree.core.runtime.notify import safe_notify
from worktree.core.step import StepExecution
from worktree.core.step.models import (
    ConditionEvaluationResult,
    ExecutionIdentity,
    LoopStepBlock,
    PreviousStepMetadata,
    StepDefinition,
    StepExecutionContext,
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
        context: dict[str, Any] | None = None,
        on_output: Callable[[str, str], None] | None = None,
        observer: RunObserver | None = None,
        failure_prompter: FailurePrompter | None = None,
        no_tty: bool = False,
        pause_store: RunPauseStore | None = None,
        step_index: int = 1,
        identity: ExecutionIdentity | None = None,
        resume_from: RunCheckpoint | None = None,
        session_tmp_dir: Path | None = None,
        session_log_dir: Path | None = None,
        save_attempt_logs: bool = True,
        session_id: str | None = None,
        artifacts_dir: Path | None = None,
        artifacts_db: ArtifactsRepository | None = None,
    ) -> None:
        self.loop = loop
        self.sandbox_path = sandbox_path.resolve()
        self.context = context or {}
        self.on_output = on_output
        self.observer = observer
        self.failure_prompter = failure_prompter
        self.no_tty = no_tty
        self.pause_store = pause_store
        self.step_index = step_index
        self.identity = identity
        self.resume_from = resume_from
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

    def _notify_sub_step_start(self, sub_idx: int, sub_step: StepDefinition) -> None:
        """Notify observer that a loop sub-step is starting."""
        safe_notify(self.observer, "on_step_start", sub_idx, len(self.loop.do), sub_step)

    def _notify_sub_step_done(self, sub_idx: int, result: StepResult) -> None:
        """Notify observer that a loop sub-step has finished."""
        safe_notify(self.observer, "on_step_done", sub_idx, len(self.loop.do), result)

    def _notify_sub_step_output(
        self,
        sub_idx: int,
        sub_step: StepDefinition,
        stream_name: str,
        line: str,
    ) -> None:
        """Notify observer of output from a loop sub-step."""
        safe_notify(self.observer, "on_step_output", sub_idx, len(self.loop.do), sub_step, line, stream=stream_name)

    def _resolve_sub_step_output_callback(
        self,
        sub_idx: int,
        sub_step: StepDefinition,
    ) -> Callable[[str, str], None] | None:
        """Construct or resolve output stream callback for a loop sub-step."""
        if self.observer is not None:
            return lambda stream, line: self._notify_sub_step_output(sub_idx, sub_step, stream, line)
        return self.on_output

    def _build_step_context(self, turn: int) -> dict[str, Any]:
        """Construct execution context dictionary for current loop turn."""
        step_context = dict(self.context)
        step_context["iteration_index"] = turn
        return step_context

    def _execute_sub_step_attempt(
        self,
        sub_step: StepDefinition,
        *,
        sub_idx: int,
        turn: int,
        attempt: int,
        historical_steps: Sequence[PreviousStepMetadata],
        state: StepLoopState,
    ) -> StepResult:
        """Execute a single attempt of a loop sub-step."""
        self._notify_sub_step_start(sub_idx, sub_step)
        on_output = self._resolve_sub_step_output_callback(sub_idx, sub_step)

        isolated_sub_step = sub_step.model_copy(update={"on_failure": OnFailureSpec(action=FailurePolicy.ABORT)})
        execution = StepExecution(
            StepExecutionContext(
                step=isolated_sub_step,
                sandbox_path=self.sandbox_path,
                context=self._build_step_context(turn),
                on_output=on_output,
                step_index=sub_idx,
                initial_attempt=attempt,
                iteration_index=turn,
                identity=self.identity,
                steps=historical_steps,
                session_tmp_dir=self.session_tmp_dir,
                session_log_dir=self.session_log_dir,
                save_attempt_logs=self.save_attempt_logs,
                loop_iteration=turn,
                session_id=self.session_id or "",
                artifacts_dir=self.artifacts_dir,
                artifacts_db=self.artifacts_db,
            )
        )
        result = execution.run()
        self._notify_sub_step_done(sub_idx, result)
        if result.ok:
            state.warnings.extend(
                auto_publish_step_artifacts(
                    sub_step,
                    sandbox_path=self.sandbox_path,
                    session_id=self.session_id or "",
                    artifacts_dir=self.artifacts_dir,
                    artifacts_db=self.artifacts_db,
                )
            )
        return result

    def _prompt_sub_step_failure(
        self,
        sub_step: StepDefinition,
        result: StepResult,
        state: StepLoopState,
    ) -> tuple[str, StepResult | None, str | None]:
        """Prompt user for decision when an interactive loop sub-step fails."""
        if self.no_tty or self.failure_prompter is None:
            warning = f"Warning: step '{sub_step.id}' requested prompt_user but run is non-interactive; aborting."
            state.warnings.append(warning)
            return LoopPromptDecision.ABORT, result, f"Step '{sub_step.id}' failed in loop '{self.loop.id}'."

        diagnostic = step_failure_diagnostic(result)
        decision = self.failure_prompter.prompt_step_failure(
            step=sub_step,
            result=result,
            diagnostic=diagnostic,
        )
        if decision == FailurePromptDecision.RETRY:
            return FailurePromptDecision.RETRY, None, None
        if decision == FailurePromptDecision.CONTINUE:
            return LoopPromptDecision.CONTINUE, mark_continued_after_prompt(result), None
        return LoopPromptDecision.ABORT, result, f"Step '{sub_step.id}' aborted by user in loop '{self.loop.id}'."

    def _handle_sub_step_result(
        self,
        sub_step: StepDefinition,
        result: StepResult,
        state: StepLoopState,
    ) -> tuple[str, StepResult | None, str | None]:
        """Handle result of a loop sub-step based on its failure policy."""
        if result.ok:
            return LoopPromptDecision.CONTINUE, result, None

        policy = effective_terminal_policy(sub_step.on_failure)
        if policy == FailurePolicy.CONTINUE:
            return LoopPromptDecision.CONTINUE, mark_continued_after_prompt(result), None
        if policy == FailurePolicy.PROMPT_USER:
            return self._prompt_sub_step_failure(sub_step, result, state)
        return LoopPromptDecision.ABORT, result, f"Step '{sub_step.id}' failed in loop '{self.loop.id}'."

    def _run_sub_step_with_retries(
        self,
        sub_step: StepDefinition,
        *,
        sub_idx: int,
        turn: int,
        state: StepLoopState,
        historical_steps: Sequence[PreviousStepMetadata],
    ) -> tuple[str, StepResult | None, str | None]:
        """Execute loop sub-step, repeating on user retry decision."""
        attempt = 1
        while True:
            result = self._execute_sub_step_attempt(
                sub_step,
                sub_idx=sub_idx,
                turn=turn,
                attempt=attempt,
                historical_steps=historical_steps,
                state=state,
            )
            action, recorded, error = self._handle_sub_step_result(sub_step, result, state)
            if action == FailurePromptDecision.RETRY:
                attempt = result.attempts + 1
                continue
            return action, recorded, error

    def _execute_turn(
        self,
        turn: int,
        state: StepLoopState,
    ) -> tuple[str, dict[str, StepResult], str | None]:
        """Execute all sub-steps in one turn of the loop."""
        turn_map: dict[str, StepResult] = {}
        historical: list[PreviousStepMetadata] = [
            previous_step_metadata_from_result(r, step_index=i + 1) for i, r in enumerate(state.step_results)
        ]
        for sub_idx, sub_step in enumerate(self.loop.do, start=1):
            action, result, error = self._run_sub_step_with_retries(
                sub_step,
                sub_idx=sub_idx,
                turn=turn,
                state=state,
                historical_steps=historical,
            )
            if result is not None:
                state.step_results.append(result)
                turn_map[sub_step.id] = result
                historical.append(previous_step_metadata_from_result(result, step_index=len(historical) + 1))
            if action == LoopPromptDecision.ABORT:
                return LoopPromptDecision.ABORT, turn_map, error
        return "ok", turn_map, None

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
    ) -> tuple[str, bool, str | None]:
        """Execute turn sub-steps and evaluate until condition status."""
        self._notify_turn(turn, max_iterations)
        status, turn_map, error = self._execute_turn(turn, state)
        if status == LoopPromptDecision.ABORT:
            self._notify_done("failed", turn)
            return LoopPromptDecision.ABORT, False, error

        all_passed, condition_results = self._evaluate_until_conditions(turn, turn_map)
        next_turn = turn + 1 if (not all_passed and turn < max_iterations) else None
        self._notify_conditions(condition_results, all_passed, next_turn)
        if all_passed:
            self._notify_done("completed", turn)
            return LoopPromptDecision.CONTINUE, True, None
        return LoopPromptDecision.CONTINUE, False, None

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

    def run(self, state: StepLoopState) -> tuple[str, StepResult | None, str | None]:
        """Execute all turns of the loop block until until conditions pass or ceiling is hit."""
        max_iterations = self.loop.max_iterations
        turn = 1
        self._notify_start(max_iterations)

        while turn <= max_iterations:
            status, passed, error = self._run_turn_cycle(turn, max_iterations, state)
            if status == LoopPromptDecision.ABORT or passed:
                return status, None, error

            if turn < max_iterations:
                turn += 1
                continue

            action, new_max, max_error = self._process_max_iteration_ceiling(turn, max_iterations, state)
            if action == LoopPromptDecision.GRANT:
                max_iterations = new_max
                turn += 1
                continue
            return action, None, max_error

        return LoopPromptDecision.CONTINUE, None, None
