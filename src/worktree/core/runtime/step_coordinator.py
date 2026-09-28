"""Per-step execution and failure-policy coordination for the multi-step run engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from worktree.common.models import FailurePolicy
from worktree.core.runtime.artifact_publish import auto_publish_step_artifacts
from worktree.core.runtime.checkpoint import Checkpoint, failed_step_message, pending_result_for_resume
from worktree.core.runtime.exceptions import PromptUserInterruptedError
from worktree.core.runtime.failure import (
    effective_terminal_policy,
    mark_continued_after_prompt,
    step_failure_diagnostic,
)
from worktree.core.runtime.log_writer import append_run_log_event
from worktree.core.runtime.models import (
    FailurePromptDecision,
    RunCheckpoint,
    RunContext,
    RunLogEvent,
    RunLogEventType,
    StepLoopState,
)
from worktree.core.runtime.notify import safe_notify
from worktree.core.step import (
    PreviousStepMetadata,
    StepDefinition,
    StepExecution,
    StepExecutionContext,
    StepResult,
)


@dataclass(frozen=True)
class StepCoordinator:
    """Per-step execution and failure-policy coordination for one run's context."""

    context: RunContext

    def build_step_context(self) -> dict[str, object] | None:
        """Build the per-step execution context, including resolved inputs."""
        step_context: dict[str, object] = {}
        if self.context.agent:
            step_context["agent"] = self.context.agent
        if self.context.inputs:
            step_context["inputs"] = self.context.inputs
        return step_context or None

    def _prompt_user_decision(
        self,
        state: StepLoopState,
        step: StepDefinition,
        result: StepResult,
        step_index: int,
    ) -> tuple[FailurePromptDecision, str | None]:
        """Resolve a ``prompt_user`` decision, degrading to abort when non-interactive."""
        diagnostic = step_failure_diagnostic(result)
        if self.context.no_tty or self.context.failure_prompter is None:
            if self.context.no_tty:
                warning = f"Warning: step '{step.id}' requested prompt_user but the run is non-interactive; aborting."
            else:
                warning = (
                    f"Warning: step '{step.id}' requested prompt_user but no failure prompter is configured; aborting."
                )
            return FailurePromptDecision.ABORT, warning

        checkpoint = Checkpoint(self.context)
        run_checkpoint = checkpoint.build(state, step=step, result=result, step_index=step_index)
        persisted = checkpoint.try_save(run_checkpoint, state.warnings)
        try:
            decision = self.context.failure_prompter.prompt_step_failure(
                step=step,
                result=result,
                diagnostic=diagnostic,
            )
        except KeyboardInterrupt:
            if persisted:
                raise PromptUserInterruptedError(run_checkpoint.diagnostic) from None
            raise
        checkpoint.try_clear(state.warnings)
        return decision, None

    @staticmethod
    def _apply_prompt_decision(
        decision: FailurePromptDecision,
        result: StepResult,
    ) -> tuple[str, StepResult | None, str | None]:
        """Map a prompt decision to orchestration action.

        Returns:
            ``(action, result_to_record, error_message)`` where action is one of
            ``retry``, ``continue``, ``abort``.
        """
        if decision == FailurePromptDecision.RETRY:
            return "retry", None, None
        if decision == FailurePromptDecision.CONTINUE:
            return "continue", mark_continued_after_prompt(result), None
        return "abort", result, failed_step_message(result)

    def _handle_failed_step(
        self,
        state: StepLoopState,
        step: StepDefinition,
        result: StepResult,
        step_index: int,
    ) -> tuple[str, StepResult | None, str | None]:
        """Apply effective terminal policy for a failed step result.

        Returns:
            ``(action, result_to_record, error_message)`` where action is one of
            ``retry``, ``continue``, ``abort``.
        """
        policy = effective_terminal_policy(step.on_failure)
        if policy == FailurePolicy.CONTINUE:
            # Defensive: step-local continue already maps to ignored; treat as non-fatal.
            return "continue", mark_continued_after_prompt(result), None
        if policy == FailurePolicy.PROMPT_USER:
            decision, warning = self._prompt_user_decision(state, step, result, step_index)
            if warning is not None:
                state.warnings.append(warning)
            return self._apply_prompt_decision(decision, result)
        return "abort", result, failed_step_message(result)

    def execute_one_step(
        self,
        state: StepLoopState,
        step: StepDefinition,
        *,
        idx: int,
        total: int,
        step_index: int,
        step_context: dict[str, object] | None,
        previous_step: PreviousStepMetadata | None = None,
        steps: Sequence[PreviousStepMetadata] | None = None,
        initial_attempt: int = 1,
        loop_iteration: int | None = None,
    ) -> tuple[str, StepResult | None, str | None]:
        """Run a step until success, continue-after-failure, or abort.

        Returns ``(action, result, error_message)`` with action ``continue`` or ``abort``.
        """
        current_attempt = initial_attempt
        while True:
            safe_notify(self.context.observer, "on_step_start", idx, total, step)
            append_run_log_event(
                state.session_log_dir,
                RunLogEvent(event=RunLogEventType.STEP_START, step_index=idx, step_id=step.id, attempt=current_attempt),
            )
            on_output = (
                (
                    lambda stream_name, line: safe_notify(
                        self.context.observer, "on_step_output", idx, total, step, line, stream=stream_name
                    )
                )
                if self.context.observer is not None
                else None
            )
            result = StepExecution(
                StepExecutionContext(
                    step=step,
                    sandbox_path=state.target_dir,
                    context=step_context,
                    on_output=on_output,
                    step_index=idx,
                    initial_attempt=current_attempt,
                    identity=self.context.identity,
                    previous_step=previous_step,
                    steps=steps,
                    session_tmp_dir=state.session_tmp_dir,
                    session_log_dir=state.session_log_dir,
                    save_attempt_logs=state.save_attempt_logs,
                    loop_iteration=loop_iteration,
                    session_id=self.context.session_id or "",
                    artifacts_dir=state.artifacts_dir,
                    artifacts_db=state.artifacts_db,
                    paths=self.context.paths,
                )
            ).run()
            safe_notify(self.context.observer, "on_step_done", idx, total, result)
            append_run_log_event(
                state.session_log_dir,
                RunLogEvent(
                    event=RunLogEventType.STEP_DONE,
                    step_index=idx,
                    step_id=step.id,
                    attempt=result.attempts,
                    status=result.status,
                    exit_code=result.exit_code,
                ),
            )
            if result.ok:
                publish_warnings = auto_publish_step_artifacts(
                    step,
                    sandbox_path=state.target_dir,
                    session_id=self.context.session_id or "",
                    artifacts_dir=state.artifacts_dir,
                    artifacts_db=state.artifacts_db,
                )
                state.warnings.extend(publish_warnings)
                return "continue", result, None
            action, recorded, error_message = self._handle_failed_step(state, step, result, step_index)
            if action == "retry":
                current_attempt = result.attempts + 1
                continue
            return action, recorded, error_message

    def resume_pending_gate(
        self,
        state: StepLoopState,
        step: StepDefinition,
        resume_checkpoint: RunCheckpoint,
        step_index: int,
        previous_step: PreviousStepMetadata | None = None,
        steps: Sequence[PreviousStepMetadata] | None = None,
    ) -> tuple[str, StepResult | None, str | None]:
        """Re-prompt at the paused step without re-executing it first."""
        result = pending_result_for_resume(resume_checkpoint, step)
        action, recorded, error_message = self._handle_failed_step(state, step, result, step_index)
        if action != "retry":
            return action, recorded, error_message
        return self.execute_one_step(
            state,
            step,
            idx=step_index + 1,
            total=len(self.context.steps),
            step_index=step_index,
            step_context=self.build_step_context(),
            previous_step=previous_step,
            steps=steps,
            initial_attempt=result.attempts + 1,
        )
