"""Contract tests for pause checkpoint construction, persistence, and resume reconstruction."""

from __future__ import annotations

from pathlib import Path

from tests.harness.builders import StepBuilder
from worktree.core.runtime.checkpoint import Checkpoint, failed_step_message, pending_result_for_resume
from worktree.core.runtime.models import RunCheckpoint, RunContext, RunPauseStore, StepLoopState
from worktree.core.sandbox import SandboxSession
from worktree.core.step.models import StepResult


class _RecordingPauseStore(RunPauseStore):
    """Test double recording every persisted checkpoint and pause-clear call."""

    def __init__(self) -> None:
        self.checkpoints: list[RunCheckpoint] = []
        self.cleared = 0

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        self.checkpoints.append(checkpoint)

    def clear_pause(self) -> None:
        self.cleared += 1


class _ExplodingPauseStore(RunPauseStore):
    """Test double raising from both RunPauseStore hooks."""

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        raise RuntimeError("disk full")

    def clear_pause(self) -> None:
        raise RuntimeError("disk full")


def _failed_result(step_id: str = "fail") -> StepResult:
    return StepResult(
        step_id=step_id,
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        error_message="Command failed with exit code 1.",
    )


class FailedStepMessageTests:
    """[tier-1/unit] failed_step_message: diagnostic message format."""

    def test_formats_step_id_and_diagnostic_detail(self) -> None:
        """[tier-1/unit] failed_step_message: renders "Step '<id>' failed: <diagnostic>" using error_message when present."""
        result = _failed_result("fail")

        message = failed_step_message(result)

        assert message == "Step 'fail' failed: Command failed with exit code 1."


class PendingResultForResumeTests:
    """[tier-1/unit] pending_result_for_resume: reuse or reconstruct the pending step result on resume."""

    def test_returns_existing_pending_result_when_present(self) -> None:
        """[tier-1/unit] pending_result_for_resume: checkpoint.pending_result is returned unchanged when set."""
        pending = _failed_result("fail")
        checkpoint = RunCheckpoint(
            next_step_index=0,
            pending_step_id="fail",
            diagnostic="Step 'fail' failed: Command failed with exit code 1.",
            pending_result=pending,
        )
        step = StepBuilder.command("exit 1").with_id("fail").build()

        result = pending_result_for_resume(checkpoint, step)

        assert result == pending

    def test_constructs_fallback_failed_result_when_pending_result_absent(self) -> None:
        """[tier-1/unit] pending_result_for_resume: with pending_result=None, constructs a failed StepResult carrying checkpoint.diagnostic."""
        checkpoint = RunCheckpoint(
            next_step_index=0,
            pending_step_id="fail",
            diagnostic="Step 'fail' failed: exit code 1",
            pending_result=None,
        )
        step = StepBuilder.command("exit 1").with_id("fail").build()

        result = pending_result_for_resume(checkpoint, step)

        assert result.step_id == "fail"
        assert result.status == "failed"
        assert result.exit_code == 1
        assert result.error_message == "Step 'fail' failed: exit code 1"


class CheckpointBuildTests:
    """[tier-1/unit] Checkpoint.build: snapshot construction from run state."""

    def test_build_with_active_sandbox_session_captures_session_fields(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.build: with an active SandboxSession, sandbox_* fields are copied from the session, not target_dir."""
        session = SandboxSession(
            session_id="sess-1",
            target_branch="worktree/sandbox-sess-1",
            sandbox_path=tmp_path / "sandbox",
            base_commit="abc123",
            name="my-sandbox",
            created_at="",
        )
        prior_result = StepResult(
            step_id="ok", status="completed", exit_code=0, stdout="", stderr="", duration_seconds=0.01
        )
        state = StepLoopState(target_dir=tmp_path, session=session, step_results=[prior_result])
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=True, keep=True, agent="claude", inputs={"k": "v"})
        step = StepBuilder.command("exit 1").with_id("fail").build()
        result = _failed_result("fail")

        checkpoint = Checkpoint(context).build(state, step=step, result=result, step_index=2)

        assert checkpoint.next_step_index == 2
        assert checkpoint.step_results == [prior_result]
        assert checkpoint.sandbox_path == str(session.sandbox_path)
        assert checkpoint.sandbox_id == "sess-1"
        assert checkpoint.sandbox_name == "my-sandbox"
        assert checkpoint.sandbox_branch == "worktree/sandbox-sess-1"
        assert checkpoint.sandbox_base_commit == "abc123"
        assert checkpoint.use_sandbox is True
        assert checkpoint.keep is True
        assert checkpoint.agent == "claude"
        assert checkpoint.inputs == {"k": "v"}
        assert checkpoint.pending_step_id == "fail"
        assert checkpoint.diagnostic == failed_step_message(result)
        assert checkpoint.pending_result == result

    def test_build_without_sandbox_session_falls_back_to_target_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.build: with session=None, sandbox_path falls back to state.target_dir and the remaining sandbox_* fields are None."""
        state = StepLoopState(target_dir=tmp_path, session=None)
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)
        step = StepBuilder.command("exit 1").with_id("fail").build()
        result = _failed_result("fail")

        checkpoint = Checkpoint(context).build(state, step=step, result=result, step_index=0)

        assert checkpoint.sandbox_path == str(tmp_path)
        assert checkpoint.sandbox_id is None
        assert checkpoint.sandbox_name is None
        assert checkpoint.sandbox_branch is None
        assert checkpoint.sandbox_base_commit is None


class CheckpointTrySaveTests:
    """[tier-1/unit] Checkpoint.try_save: best-effort checkpoint persistence."""

    def test_no_pause_store_returns_false_without_saving(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.try_save: context.pause_store=None returns False and appends no warnings."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, pause_store=None)
        checkpoint = RunCheckpoint(next_step_index=0, pending_step_id="fail", diagnostic="boom")
        warnings: list[str] = []

        saved = Checkpoint(context).try_save(checkpoint, warnings)

        assert saved is False
        assert warnings == []

    def test_pause_store_success_returns_true_and_saves_checkpoint(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.try_save: a working pause_store receives the checkpoint and returns True."""
        store = _RecordingPauseStore()
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, pause_store=store)
        checkpoint = RunCheckpoint(next_step_index=0, pending_step_id="fail", diagnostic="boom")
        warnings: list[str] = []

        saved = Checkpoint(context).try_save(checkpoint, warnings)

        assert saved is True
        assert store.checkpoints == [checkpoint]
        assert warnings == []

    def test_pause_store_exception_returns_false_and_appends_warning(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.try_save: a raising pause_store returns False and appends a warning naming the failure."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, pause_store=_ExplodingPauseStore())
        checkpoint = RunCheckpoint(next_step_index=0, pending_step_id="fail", diagnostic="boom")
        warnings: list[str] = []

        saved = Checkpoint(context).try_save(checkpoint, warnings)

        assert saved is False
        assert len(warnings) == 1
        assert "disk full" in warnings[0]


class CheckpointTryClearTests:
    """[tier-1/unit] Checkpoint.try_clear: best-effort pause-state clearing."""

    def test_no_pause_store_is_noop(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.try_clear: context.pause_store=None appends no warnings."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, pause_store=None)
        warnings: list[str] = []

        Checkpoint(context).try_clear(warnings)

        assert warnings == []

    def test_pause_store_success_clears_pause(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.try_clear: a working pause_store's clear_pause is invoked exactly once."""
        store = _RecordingPauseStore()
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, pause_store=store)
        warnings: list[str] = []

        Checkpoint(context).try_clear(warnings)

        assert store.cleared == 1
        assert warnings == []

    def test_pause_store_exception_appends_warning(self, tmp_path: Path) -> None:
        """[tier-1/unit] Checkpoint.try_clear: a raising pause_store appends a warning naming the failure instead of propagating."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, pause_store=_ExplodingPauseStore())
        warnings: list[str] = []

        Checkpoint(context).try_clear(warnings)

        assert len(warnings) == 1
        assert "disk full" in warnings[0]
