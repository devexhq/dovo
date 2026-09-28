"""Pause checkpoint construction, persistence, and resume-result reconstruction."""

from __future__ import annotations

from dataclasses import dataclass

from worktree.core.runtime.failure import step_failure_diagnostic
from worktree.core.runtime.models import RunCheckpoint, RunContext, StepLoopState
from worktree.core.step import StepDefinition, StepResult


def failed_step_message(result: StepResult) -> str:
    """Format diagnostic message describing step failure."""
    detail = step_failure_diagnostic(result)
    return f"Step '{result.step_id}' failed: {detail}"


def pending_result_for_resume(checkpoint: RunCheckpoint, step: StepDefinition) -> StepResult:
    """Return existing pending step result from checkpoint or construct fallback."""
    if checkpoint.pending_result is not None:
        return checkpoint.pending_result
    return StepResult(
        step_id=step.id,
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        error_message=checkpoint.diagnostic,
    )


@dataclass(frozen=True)
class Checkpoint:
    """Builds, persists, and clears pause checkpoints for one run's context."""

    context: RunContext

    def build(
        self,
        state: StepLoopState,
        *,
        step: StepDefinition,
        result: StepResult,
        step_index: int,
    ) -> RunCheckpoint:
        """Construct RunCheckpoint snapshot for pausing execution."""
        diagnostic = failed_step_message(result)
        session = state.session
        return RunCheckpoint(
            next_step_index=step_index,
            step_results=list(state.step_results),
            sandbox_path=str(session.sandbox_path if session is not None else state.target_dir),
            sandbox_id=session.session_id if session is not None else None,
            sandbox_name=session.name if session is not None else None,
            sandbox_branch=session.target_branch if session is not None else None,
            sandbox_base_commit=session.base_commit if session is not None else None,
            use_sandbox=self.context.use_sandbox,
            keep=self.context.keep,
            agent=self.context.agent,
            inputs=dict(self.context.inputs or {}),
            identity=self.context.identity,
            pending_step_id=step.id,
            diagnostic=diagnostic,
            pending_result=result,
        )

    def try_save(self, checkpoint: RunCheckpoint, warnings: list[str]) -> bool:
        """Attempt to save execution checkpoint to pause store, recording any warnings."""
        if self.context.pause_store is None:
            return False
        try:
            self.context.pause_store.save_checkpoint(checkpoint)
        except Exception as exc:
            warnings.append(f"Failed to persist pause checkpoint: {exc}")
            return False
        return True

    def try_clear(self, warnings: list[str]) -> None:
        """Attempt to clear persisted pause state, recording any warnings."""
        if self.context.pause_store is None:
            return
        try:
            self.context.pause_store.clear_pause()
        except Exception as exc:
            warnings.append(f"Failed to clear pause checkpoint: {exc}")
