"""Single-tier CLI integration tests for dovo worktree list."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeListResult, WorktreeListStatus
from dovo.core.worktree.worktree import Worktree
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.workspace_paths import initialized_workspace_paths


class WorktreeListCliIntegrationTests:
    """Typer runner integration tests for dovo worktree list."""

    def test_worktree_list_cli_empty_workspace_renders_no_worktrees(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeListResult]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list"])

        assert result.exit_code == 0
        assert "No worktrees found." in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeListStatus.OK
        assert len(payload.worktrees) == 0

    def test_worktree_list_cli_renders_created_worktree_in_table(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeListResult]
    ) -> None:
        create_result = Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="listed")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list"])

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
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeListResult]
    ) -> None:
        Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="listed")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list", "--status", "merged"])

        assert result.exit_code == 0
        assert "No worktrees found." in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == WorktreeListStatus.OK
        assert len(payload.worktrees) == 0

    def test_worktree_list_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorktreeListResult",
            "payload": {
                "status": "ok",
                "worktrees": [],
                "error_code": None,
                "errors": [],
                "warnings": [],
                "remediations": [],
                "diagnostics": [],
            },
        }
