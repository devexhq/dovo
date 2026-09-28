"""Single-tier CLI integration tests for wt sandbox show."""

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
from worktree.core.sandbox.models import SandboxShowResult, SandboxShowStatus


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class SandboxShowCliIntegrationTests:
    """Typer runner integration tests for wt sandbox show."""

    def test_sandbox_show_cli_existing_sandbox_exits_zero(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox show <id> on an existing sandbox exits 0, renders its session_id, and dispatches the exact record."""
        create_result = Sandbox(paths=_paths_for(sandbox_workspace)).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "show", session.session_id])

        assert result.exit_code == 0
        assert session.session_id in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, SandboxShowResult)
        assert result_dto.status == SandboxShowStatus.OK
        assert result_dto.sandbox is not None
        assert result_dto.sandbox.id == session.session_id
        assert result_dto.sandbox.name == "show-me"
        assert result_dto.sandbox.branch_name == session.target_branch
        assert result_dto.sandbox.base_commit == session.base_commit
        assert result_dto.sandbox.sandbox_path == session.sandbox_path
        assert result_dto.sandbox.status == SandboxStatus.ACTIVE
        assert result_dto.disk_present is True
        assert result_dto.reconciled is False
        assert len(result_dto.errors) == 0
        assert len(result_dto.warnings) == 0
        assert len(result_dto.fixes) == 0

    def test_sandbox_show_cli_missing_sandbox_exits_one(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox show on a missing id exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, SandboxShowResult)
        assert result_dto.status == SandboxShowStatus.NOT_FOUND
        assert result_dto.sandbox is None
        assert result_dto.disk_present is False
        assert result_dto.reconciled is False
        assert result_dto.errors == ["Sandbox 'missing-id' not found."]
        assert len(result_dto.warnings) == 0
        assert result_dto.fixes == ["Run `wt sandbox list` to see known sandboxes"]

    def test_sandbox_show_cli_renders_json(self, cli_runner: CliRunner, sandbox_workspace: Path) -> None:
        """wt sandbox show <id> --format json emits a SandboxShowResult envelope matching the seeded record."""
        create_result = Sandbox(paths=_paths_for(sandbox_workspace)).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(
            app,
            ["-p", str(sandbox_workspace), "sandbox", "show", session.session_id, "--format", "json"],
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        sandbox = actual_json["payload"]["sandbox"]
        for field in ("created_at", "updated_at", "project_id"):
            assert sandbox[field]
            sandbox[field] = "placeholder"

        assert actual_json == {
            "event_type": "SandboxShowResult",
            "payload": {
                "status": "ok",
                "sandbox": {
                    "id": session.session_id,
                    "project_id": "placeholder",
                    "name": "show-me",
                    "branch_name": session.target_branch,
                    "base_commit": session.base_commit,
                    "sandbox_path": str(session.sandbox_path),
                    "status": "active",
                    "created_at": "placeholder",
                    "updated_at": "placeholder",
                },
                "disk_present": True,
                "reconciled": False,
                "errors": [],
                "warnings": [],
                "fixes": [],
                "error_code": None,
            },
        }
