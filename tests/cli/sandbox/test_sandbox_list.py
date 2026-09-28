"""Single-tier CLI integration tests for wt sandbox list."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db import SandboxStatus
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.sandbox.facade import Sandbox
from worktree.core.sandbox.models import SandboxListStatus


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class SandboxListCliIntegrationTests:
    """Typer runner integration tests for wt sandbox list."""

    def test_sandbox_list_cli_empty_workspace_renders_no_sandboxes(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox list against an empty workspace exits 0, reports no sandboxes, and dispatches the exact empty DTO."""
        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "list"])

        assert result.exit_code == 0
        assert "No sandboxes found." in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == SandboxListStatus.OK
        assert len(payload.sandboxes) == 0

    def test_sandbox_list_cli_renders_created_sandbox_in_table(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox list renders a facade-created sandbox's session_id in the terminal table and dispatches its exact record."""
        create_result = Sandbox(paths=_paths_for(sandbox_workspace)).create(name="listed")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "list"])

        assert result.exit_code == 0
        assert session.session_id in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == SandboxListStatus.OK
        assert len(payload.sandboxes) == 1

        sandbox = payload.sandboxes[0]
        assert sandbox.id == session.session_id
        assert sandbox.name == "listed"
        assert sandbox.branch_name == session.target_branch
        assert sandbox.base_commit == session.base_commit
        assert sandbox.sandbox_path == session.sandbox_path
        assert sandbox.status == SandboxStatus.ACTIVE

    def test_sandbox_list_cli_status_filter_excludes_non_matching(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox list --status merged excludes an active sandbox, reports no sandboxes, and dispatches an empty DTO."""
        Sandbox(paths=_paths_for(sandbox_workspace)).create(name="listed")

        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "list", "--status", "merged"])

        assert result.exit_code == 0
        assert "No sandboxes found." in result.stdout
        assert len(dispatch_spy) == 1
        payload = dispatch_spy[0]
        assert payload.status == SandboxListStatus.OK
        assert len(payload.sandboxes) == 0

    def test_sandbox_list_cli_renders_json(self, cli_runner: CliRunner, sandbox_workspace: Path) -> None:
        """wt sandbox list --format json against an empty workspace emits a SandboxListResult envelope."""
        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "SandboxListResult",
            "payload": {"status": "ok", "sandboxes": [], "error_code": None, "errors": [], "warnings": [], "fixes": []},
        }
