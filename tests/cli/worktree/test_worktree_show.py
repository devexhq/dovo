"""Single-tier CLI integration tests for dovo worktree show."""

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
from dovo.core.worktree.models import WorktreeShowResult, WorktreeShowStatus


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class WorktreeShowCliIntegrationTests:
    """Typer runner integration tests for dovo worktree show."""

    def test_worktree_show_cli_existing_worktree_exits_zero(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree show <id> on an existing worktree exits 0, renders its session_id, and dispatches the exact record."""
        create_result = Worktree(paths=_paths_for(worktree_workspace)).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "show", session.session_id])

        assert result.exit_code == 0
        assert session.session_id in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeShowResult)
        assert result_dto.status == WorktreeShowStatus.OK
        assert result_dto.worktree is not None
        assert result_dto.worktree.id == session.session_id
        assert result_dto.worktree.name == "show-me"
        assert result_dto.worktree.branch_name == session.target_branch
        assert result_dto.worktree.base_commit == session.base_commit
        assert result_dto.worktree.worktree_path == session.worktree_path
        assert result_dto.worktree.status == WorktreeStatus.ACTIVE
        assert result_dto.disk_present is True
        assert result_dto.reconciled is False
        assert len(result_dto.errors) == 0
        assert len(result_dto.warnings) == 0
        assert len(result_dto.fixes) == 0

    def test_worktree_show_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree show on a missing id exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeShowResult)
        assert result_dto.status == WorktreeShowStatus.NOT_FOUND
        assert result_dto.worktree is None
        assert result_dto.disk_present is False
        assert result_dto.reconciled is False
        assert result_dto.errors == ["Worktree 'missing-id' not found."]
        assert len(result_dto.warnings) == 0
        assert result_dto.fixes == ["Run `dovo worktree list` to see known worktrees"]

    def test_worktree_show_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree show <id> --format json emits a WorktreeShowResult envelope matching the seeded record."""
        create_result = Worktree(paths=_paths_for(worktree_workspace)).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(
            app,
            ["-p", str(worktree_workspace), "worktree", "show", session.session_id, "--format", "json"],
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        worktree = actual_json["payload"]["worktree"]
        for field in ("created_at", "updated_at", "project_id"):
            assert worktree[field]
            worktree[field] = "placeholder"

        assert actual_json == {
            "event_type": "WorktreeShowResult",
            "payload": {
                "status": "ok",
                "worktree": {
                    "id": session.session_id,
                    "project_id": "placeholder",
                    "name": "show-me",
                    "branch_name": session.target_branch,
                    "base_commit": session.base_commit,
                    "worktree_path": str(session.worktree_path),
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
