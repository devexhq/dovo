"""Single-tier CLI integration tests for dovo worktree apply."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeApplyResult, WorktreeApplyStatus, WorktreeApplyStrategy, WorktreeSession
from dovo.core.worktree.worktree import Worktree
from tests.harness.workspace_paths import initialized_workspace_paths


def _create_worktree_with_committed_change(cli_workspace: Path) -> WorktreeSession:
    """Create a worktree and commit one file change inside its worktree."""
    create_result = Worktree(paths=initialized_workspace_paths(cli_workspace)).create(name="apply-me")
    assert create_result.session is not None
    session = create_result.session

    (session.worktree_path / "target.py").write_text("changed\n", encoding="utf-8")
    GitRunner.add_all(session.worktree_path)
    GitRunner.commit(session.worktree_path, "Worktree change to target.py")

    return session


class WorktreeApplyCliIntegrationTests:
    """Typer runner integration tests for dovo worktree apply."""

    def test_worktree_apply_cli_patch_strategy_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id])

        assert result.exit_code == 0
        assert "Applied worktree" in result.stdout
        assert (cli_workspace / "target.py").read_text(encoding="utf-8") == "changed\n"
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].status == WorktreeApplyStatus.OK
        assert dispatch_spy[0].worktree_id == session.session_id
        assert dispatch_spy[0].strategy == WorktreeApplyStrategy.PATCH

    def test_worktree_apply_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "apply", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].status == WorktreeApplyStatus.NOT_FOUND
        assert dispatch_spy[0].worktree_id == "missing-id"
        assert dispatch_spy[0].errors == ["Worktree 'missing-id' not found."]

    def test_worktree_apply_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeApplyResult"
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["touched_files"] == ["target.py"]
