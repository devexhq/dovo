"""Per-step execution and failure-policy coordination for the multi-step run engine."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.interaction import InteractionAnswer, InteractionRequest
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors import StepExecution
from dovo.engine.executors.agent_step import build_agent_step_runner
from dovo.engine.executors.ask_step import complete_ask_step, resolve_ask_request
from dovo.engine.executors.models import (
    AskResolution,
    PreviousStepMetadata,
    ProgressCallback,
    StepExecutionContext,
    StepResult,
)
from dovo.engine.executors.session_log import append_session_log_event
from dovo.engine.failure import (
    failed_step_message,
    mark_continued_after_prompt,
    step_failure_diagnostic,
)
from dovo.engine.models import FailurePromptDecision, RunContext, RunSettings, StepAction
from dovo.engine.notify import safe_notify


@dataclass(frozen=True)
class StepCoordinator:
    """Per-step execution and failure-policy coordination for one run's context."""

    context: RunSettings

    @property
    def is_interactive(self) -> bool:
        """Return True when the run has a failure prompter and is not running with no_tty."""
        return not self.context.no_tty and self.context.failure_prompter is not None

    def prompt_decision(
        self,
        step: StepDefinition,
        result: StepResult,
    ) -> tuple[FailurePromptDecision, str | None]:
        """Ask the prompter for a decision, degrading to abort with a warning when non-interactive."""
        diagnostic = step_failure_diagnostic(result)
        if self.context.no_tty or self.context.failure_prompter is None:
            if self.context.no_tty:
                warning = f"Warning: step '{step.id}' requested prompt_user but the run is non-interactive; aborting."
            else:
                warning = (
                    f"Warning: step '{step.id}' requested prompt_user but no failure prompter is configured; aborting."
                )
            return FailurePromptDecision.ABORT, warning

        decision = self.context.failure_prompter.prompt_step_failure(
            step=step,
            result=result,
            diagnostic=diagnostic,
        )
        return decision, None

    @staticmethod
    def apply_prompt_decision(
        decision: FailurePromptDecision,
        result: StepResult,
    ) -> tuple[StepAction, StepResult | None, str | None]:
        """Map a prompt decision to ``(StepAction, result_to_record, error_message)``."""
        if decision == FailurePromptDecision.RETRY:
            return StepAction.RETRY, None, None
        if decision == FailurePromptDecision.CONTINUE:
            return StepAction.CONTINUE, mark_continued_after_prompt(result), None
        return StepAction.ABORT, result, failed_step_message(result)

    def run_attempt(
        self,
        run_context: RunContext,
        warnings: list[str],
        step: StepDefinition,
        *,
        idx: int,
        total: int,
        previous_step: PreviousStepMetadata | None = None,
        steps: Sequence[PreviousStepMetadata] | None = None,
        initial_attempt: int = 1,
        loop_iteration: int | None = None,
        loop_id: str | None = None,
    ) -> StepResult:
        """Notify, log, run one StepExecution, and forward the result's warnings to the caller's run warnings."""
        self._log_step_start(run_context, step, idx, total, initial_attempt, loop_id, loop_iteration)
        on_output = (
            (
                lambda stream_name, line: safe_notify(
                    self.context.observer, "on_step_output", idx, total, step, line, stream=stream_name
                )
            )
            if self.context.observer is not None
            else None
        )
        on_progress = (
            (lambda update: safe_notify(self.context.observer, "on_step_progress", idx, total, step, update))
            if self.context.observer is not None
            else None
        )
        result = StepExecution(
            self._execution_context(
                run_context,
                step,
                idx=idx,
                previous_step=previous_step,
                steps=steps,
                initial_attempt=initial_attempt,
                loop_iteration=loop_iteration,
                loop_id=loop_id,
                on_output=on_output,
                on_progress=on_progress,
            )
        ).run()
        self._log_step_done(run_context, step, idx, total, result, loop_id, loop_iteration)
        warnings.extend(result.warnings)
        return result

    def _execution_context(
        self,
        run_context: RunContext,
        step: StepDefinition,
        *,
        idx: int,
        previous_step: PreviousStepMetadata | None,
        steps: Sequence[PreviousStepMetadata] | None,
        initial_attempt: int,
        loop_iteration: int | None,
        loop_id: str | None,
        on_output: Callable[[str, str], None] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> StepExecutionContext:
        """Build the StepExecutionContext shared by command, script, agent, internal and ask steps."""
        session_dir = self.context.paths.session_dir(self.context.session_id) if self.context.session_id else None
        return StepExecutionContext(
            step=step,
            worktree_path=run_context.target_dir,
            inputs=self.context.inputs or None,
            on_output=on_output,
            on_progress=on_progress,
            step_index=idx,
            initial_attempt=initial_attempt,
            identity=self.context.identity,
            previous_step=previous_step,
            steps=steps,
            session_tmp_dir=run_context.session_tmp_dir,
            session_log_dir=run_context.session_log_dir,
            save_attempt_logs=run_context.save_attempt_logs,
            loop_iteration=loop_iteration,
            loop_id=loop_id,
            session_id=self.context.session_id or "",
            artifact_store=run_context.artifact_store,
            paths=self.context.paths,
            agent_runner=None
            if step.type == StepType.ASK
            else build_agent_step_runner(
                self.context.agent,
                run_context.worktree is not None,
                session_tmp_dir=run_context.session_tmp_dir,
                main_checkout=self.context.paths.root_dir,
                session_log_dir=run_context.session_log_dir,
                session_dir=session_dir,
            ),
            sensitive_variables=self.context.sensitive_variables,
        )

    def _log_step_start(
        self,
        run_context: RunContext,
        step: StepDefinition,
        idx: int,
        total: int,
        attempt: int,
        loop_id: str | None,
        loop_iteration: int | None,
    ) -> None:
        """Notify the observer and append STEP_START for one attempt."""
        safe_notify(self.context.observer, "on_step_start", idx, total, step)
        append_session_log_event(
            run_context.session_log_dir,
            SessionLogEvent(
                event=SessionLogEventType.STEP_START,
                step_index=idx,
                step_id=step.id,
                step_name=step.name,
                attempt=attempt,
                loop_id=loop_id,
                iteration=loop_iteration,
            ),
        )

    def _log_step_done(
        self,
        run_context: RunContext,
        step: StepDefinition,
        idx: int,
        total: int,
        result: StepResult,
        loop_id: str | None,
        loop_iteration: int | None,
    ) -> None:
        """Notify the observer and append STEP_DONE for a settled attempt."""
        safe_notify(self.context.observer, "on_step_done", idx, total, step, result)
        append_session_log_event(
            run_context.session_log_dir,
            SessionLogEvent(
                event=SessionLogEventType.STEP_DONE,
                step_index=idx,
                step_id=step.id,
                step_name=step.name,
                attempt=result.attempts,
                status=result.status,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                loop_id=loop_id,
                iteration=loop_iteration,
            ),
        )

    def resolve_ask(
        self,
        run_context: RunContext,
        step: StepDefinition,
        *,
        index: int,
        total: int,
        previous_step: PreviousStepMetadata | None,
        steps: Sequence[PreviousStepMetadata] | None,
        attempt: int,
        loop_iteration: int | None,
        loop_id: str | None,
    ) -> AskResolution:
        """Notify and log the step start, then resolve its request."""
        self._log_step_start(run_context, step, index, total, attempt, loop_id, loop_iteration)
        resolution = resolve_ask_request(
            self._execution_context(
                run_context,
                step,
                idx=index,
                previous_step=previous_step,
                steps=steps,
                initial_attempt=attempt,
                loop_iteration=loop_iteration,
                loop_id=loop_id,
            )
        )
        if resolution.result is not None:
            self._log_step_done(run_context, step, index, total, resolution.result, loop_id, loop_iteration)
        return resolution

    def complete_ask(
        self,
        run_context: RunContext,
        step: StepDefinition,
        request: InteractionRequest,
        answer: InteractionAnswer,
        *,
        index: int,
        total: int,
        previous_step: PreviousStepMetadata | None,
        steps: Sequence[PreviousStepMetadata] | None,
        attempt: int,
        loop_iteration: int | None,
        loop_id: str | None,
    ) -> StepResult:
        """Build the answered step's result, then notify and log the step done."""
        result = complete_ask_step(
            self._execution_context(
                run_context,
                step,
                idx=index,
                previous_step=previous_step,
                steps=steps,
                initial_attempt=attempt,
                loop_iteration=loop_iteration,
                loop_id=loop_id,
            ),
            request,
            answer,
        )
        self._log_step_done(run_context, step, index, total, result, loop_id, loop_iteration)
        return result
