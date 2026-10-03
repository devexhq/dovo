"""Contract tests for type=internal step dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.catalog.definitions import StepDefinition, StepType
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
