"""Per-step execution and failure-policy coordination for the multi-step run engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from worktree.core.artifacts.services.upload import publish_artifact
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.engine.failure import (
    failed_step_message,
    mark_continued_after_prompt,
    step_failure_diagnostic,
)
from worktree.core.engine.models import FailurePromptDecision, RunContext, StepLoopState
from worktree.core.engine.notify import safe_notify
from worktree.core.logs import RunLogEvent, RunLogEventType, append_run_log_event
from worktree.core.step import (
    PreviousStepMetadata,
    StepDefinition,
    StepExecution,
    StepExecutionContext,
    StepResult,
)


def auto_publish_step_artifacts(
    step: StepDefinition,
    *,
    sandbox_path: Path,
    session_id: str,
    artifacts_dir: Path | None,
    artifacts_db: ArtifactsRepository | None,
) -> list[str]:
    """Publish every artifacts: entry declared on step via the shared publish_artifact service; return warnings, never raise."""
    if not step.artifacts or artifacts_dir is None or artifacts_db is None:
        return []

    warnings: list[str] = []
    for spec in step.artifacts:
        try:
            result = publish_artifact(
                sandbox_path,
                artifacts_dir,
                artifacts_db,
                session_id=session_id,
                name=spec.name,
                path_glob=spec.path,
                retention_days=spec.retention_days,
            )
        except Exception as exc:
            warnings.append(f"Failed to publish artifact '{spec.name}': {exc}")
            continue
        if not result.ok:
            detail = "; ".join(result.errors) or "publish failed"
            warnings.append(f"Failed to publish artifact '{spec.name}': {detail}")

    return warnings


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

    def run_attempt(
        self,
        state: StepLoopState,
        step: StepDefinition,
        *,
        idx: int,
        total: int,
        step_context: dict[str, object] | None,
        previous_step: PreviousStepMetadata | None = None,
        steps: Sequence[PreviousStepMetadata] | None = None,
        initial_attempt: int = 1,
        loop_iteration: int | None = None,
    ) -> StepResult:
        """Notify, log, run one StepExecution, and auto-publish artifacts when it succeeds."""
        safe_notify(self.context.observer, "on_step_start", idx, total, step)
        append_run_log_event(
            state.session_log_dir,
            RunLogEvent(event=RunLogEventType.STEP_START, step_index=idx, step_id=step.id, attempt=initial_attempt),
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
                initial_attempt=initial_attempt,
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
        return result
