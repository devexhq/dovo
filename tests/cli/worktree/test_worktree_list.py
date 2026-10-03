"""Single-tier CLI integration tests for dovo worktree list."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import WorktreeStatus
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree.facade import Worktree
from dovo.core.worktree.models import WorktreeListStatus


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class WorktreeListCliIntegrationTests:
    """Typer runner integration tests for dovo worktree list."""

    def test_worktree_list_cli_empty_workspace_renders_no_worktrees(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree list against an empty workspace exits 0, reports no worktrees, and dispatches the exact empty DTO."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "list"])

        assert result.exit_code == 0
        assert "No worktrees found." in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeListStatus.OK
        assert len(payload.worktrees) == 0

    def test_worktree_list_cli_renders_created_worktree_in_table(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree list renders a facade-created worktree's session_id in the terminal table and dispatches its exact record."""
        create_result = Worktree(paths=_paths_for(worktree_workspace)).create(name="listed")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "list"])

        assert result.exit_code == 0
        assert session.session_id in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeListStatus.OK
        assert len(payload.worktrees) == 1

        worktree = payload.worktrees[0]
        assert worktree.id == session.session_id
        assert worktree.name == "listed"
        assert worktree.branch_name == session.target_branch
        assert worktree.base_commit == session.base_commit
        assert worktree.worktree_path == session.worktree_path
        assert worktree.status == WorktreeStatus.ACTIVE

    def test_worktree_list_cli_status_filter_excludes_non_matching(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree list --status merged excludes an active worktree, reports no worktrees, and dispatches an empty DTO."""
        Worktree(paths=_paths_for(worktree_workspace)).create(name="listed")

        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "list", "--status", "merged"])

        assert result.exit_code == 0
        assert "No worktrees found." in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeListStatus.OK
        assert len(payload.worktrees) == 0

    def test_worktree_list_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree list --format json against an empty workspace emits a WorktreeListResult envelope."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorktreeListResult",
            "payload": {"status": "ok", "worktrees": [], "error_code": None, "errors": [], "warnings": [], "fixes": []},
        }
