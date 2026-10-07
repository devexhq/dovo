"""Single-tier CLI integration tests for dovo worktree apply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.facade import Worktree
from dovo.core.worktree.models import WorktreeApplyStatus, WorktreeApplyStrategy, WorktreeSession
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _create_worktree_with_committed_change(worktree_workspace: Path) -> WorktreeSession:
    """Create a worktree and commit one file change inside its worktree."""
    create_result = Worktree(paths=_paths_for(worktree_workspace)).create(name="apply-me")
    assert create_result.session is not None
    session = create_result.session

    (session.worktree_path / "target.py").write_text("changed\n", encoding="utf-8")
    GitRunner.add_all(session.worktree_path)
    GitRunner.commit(session.worktree_path, "Worktree change to target.py")

    return session


class WorktreeApplyCliIntegrationTests:
    """Typer runner integration tests for dovo worktree apply."""

    def test_worktree_apply_cli_patch_strategy_exits_zero(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree apply <id> (patch strategy) writes the change to the main tree, exits 0, and dispatches the exact WorktreeApplyResult DTO."""
        session = _create_worktree_with_committed_change(worktree_workspace)

        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "apply", session.session_id])

        assert result.exit_code == 0
        assert "Applied worktree" in result.stdout
        assert (worktree_workspace / "target.py").read_text(encoding="utf-8") == "changed\n"
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].status == WorktreeApplyStatus.OK
        assert dispatch_spy[0].worktree_id == session.session_id
        assert dispatch_spy[0].strategy == WorktreeApplyStrategy.PATCH
        assert dispatch_spy[0].touched_files == ["target.py"]
        assert dispatch_spy[0].conflicting_files == []
        assert dispatch_spy[0].cleaned_up is False
        assert dispatch_spy[0].commit_sha is None
        assert dispatch_spy[0].errors == []
        assert dispatch_spy[0].warnings == []
        assert dispatch_spy[0].fixes == []

    def test_worktree_apply_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree apply on a missing id exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "apply", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].status == WorktreeApplyStatus.NOT_FOUND
        assert dispatch_spy[0].worktree_id == "missing-id"
        assert dispatch_spy[0].strategy == WorktreeApplyStrategy.PATCH
        assert dispatch_spy[0].touched_files == []
        assert dispatch_spy[0].conflicting_files == []
        assert dispatch_spy[0].cleaned_up is False
        assert dispatch_spy[0].commit_sha is None
        assert dispatch_spy[0].errors == ["Worktree 'missing-id' not found."]
        assert dispatch_spy[0].warnings == []
        assert dispatch_spy[0].fixes == ["Run `dovo worktree list` to see known worktrees"]

    def test_worktree_apply_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree apply <id> --format json emits a WorktreeApplyResult envelope."""
        session = _create_worktree_with_committed_change(worktree_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(worktree_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorktreeApplyResult",
            "payload": {
                "status": "ok",
                "worktree_id": session.session_id,
                "strategy": "patch",
                "touched_files": ["target.py"],
                "conflicting_files": [],
                "cleaned_up": False,
                "commit_sha": None,
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
