"""Contract tests for type=internal step dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.engine.executors import step_executor
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution


class InternalCommandDispatchTests:
    def test_execute_internal_unknown_command_returns_failed_dispatch(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.nonexistent")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "artifacts.nonexistent" in result.error_message

    def test_execute_internal_handler_exception_returns_failed_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        def _broken_handler(ctx: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setitem(step_executor.INTERNAL_COMMAND_HANDLERS, "artifacts.upload", _broken_handler)
        step = StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "boom" in result.error_message

    def test_execute_internal_no_active_session_fails_with_clear_message(self, tmp_path: Path) -> None:
        """Fail naming the missing session, not an AttributeError, when artifact_store is None."""
        step = StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "session" in result.error_message.lower()

    def test_loop_iteration_and_attempt_reach_upload_handler(
        self, tmp_path: Path, engine_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository
    ) -> None:
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
                artifact_store=ArtifactStore(Artifacts(engine_paths, artifacts_repository), "wf_1"),
                loop_iteration=2,
                diagnostics=[],
            )
        ).run()

        record = artifacts_repository.get("wf_1", "plan")
        assert result.status == "completed"
        assert record is not None
        assert (record.step_id, record.attempt, record.loop_iteration) == ("upload-plan", 1, 2)
