"""Contract tests for type=internal step dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors import step_executor
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution


class InternalCommandDispatchTests:
    """[tier-1/unit] StepExecution._execute_internal: registry lookup, handler exceptions, and missing-session dispatch."""

    def test_execute_internal_unknown_command_returns_failed_dispatch(self, tmp_path: Path) -> None:
        """[tier-1/unit] _execute_internal: command='artifacts.nonexistent' returns a failed StepDispatchOutcome naming the unknown command, without raising."""
        step = StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.nonexistent")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "artifacts.nonexistent" in result.error_message

    def test_execute_internal_handler_exception_returns_failed_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] _execute_internal: a registered handler raising an unexpected exception is caught into a failed StepDispatchOutcome, not propagated."""

        def _broken_handler(ctx: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setitem(step_executor.INTERNAL_COMMAND_HANDLERS, "artifacts.upload", _broken_handler)
        step = StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "boom" in result.error_message

    def test_execute_internal_no_active_session_fails_with_clear_message(self, tmp_path: Path) -> None:
        """[tier-1/unit] _execute_internal: artifacts_dir/artifacts_db both None (no session_id) dispatches artifacts.upload to a failed StepDispatchOutcome naming the missing session, not an unhandled AttributeError."""
        step = StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "session" in result.error_message.lower()

    def test_loop_iteration_and_attempt_reach_upload_handler(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
        """[tier-1/integration] StepExecution.run: artifacts.upload step with loop_iteration=2 on attempt 1 -> stored row loop_iteration 2, attempt 1, step_id == step.id."""
        (tmp_path / "PLAN.md").write_text("plan", encoding="utf-8")
        step = StepDefinition(
            id="upload-plan",
            type=StepType.INTERNAL,
            command="artifacts.upload",
            env={"ARTIFACT_NAME": "plan", "ARTIFACT_PATH": "PLAN.md"},
        )

        result = StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                session_id="wf_1",
                artifacts_dir=engine_paths.artifacts_dir,
                artifacts_db=artifacts_repository,
                loop_iteration=2,
            )
        ).run()

        record = artifacts_repository.get("wf_1", "plan")
        assert result.status == "completed"
        assert record is not None
        assert (record.step_id, record.attempt, record.loop_iteration) == ("upload-plan", 1, 2)
