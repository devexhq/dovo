"""Single-tier CLI integration tests for wt status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.config.loader import ConfigLoadStatus
from worktree.core.db.connection import resolve_db_path
from worktree.core.status.models import (
    WorktreeStatusResult,
)

_BRANCH_WARNING = "Active branch is 'main'. Automated workflows on primary branches are discouraged."
_AGENT_MODEL_WARNING = "Agent model is not configured (agent.model is null)."


class StatusCliIntegrationTests:
    """Typer runner integration tests for wt status."""

    def test_status_cli_clean_worktree_exits_zero(
        self, cli_runner: CliRunner, status_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt status on a clean git worktree exits 0, renders the bare branch name, and dispatches the exact DTO."""
        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status"])

        assert result.exit_code == 0
        assert "main" in result.stdout
        assert "(dirty)" not in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeStatusResult)
        assert result_dto.root_dir == status_workspace
        assert result_dto.is_initialized is True
        assert result_dto.git.is_git_repo is True
        assert result_dto.git.branch == "main"
        assert result_dto.git.is_dirty is False
        assert result_dto.git.uncommitted_files == 0
        assert result_dto.config.status == ConfigLoadStatus.OK
        assert result_dto.config.config_path == status_workspace / ".worktree" / "config.json"
        assert result_dto.config.is_valid is True
        assert result_dto.catalog.exists is False
        assert result_dto.catalog.total_items == 0
        assert result_dto.database.exists is True
        assert result_dto.database.db_path == resolve_db_path(resolve_global_paths(None))
        assert result_dto.database.is_accessible is True
        assert result_dto.sandboxes.active_sandboxes == 0
        assert result_dto.sandboxes.max_active_sandboxes == 3
        assert result_dto.warnings == [_BRANCH_WARNING, _AGENT_MODEL_WARNING]

    def test_status_cli_dirty_worktree_shows_dirty_branch_exits_zero(
        self, cli_runner: CliRunner, status_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt status with one untracked file exits 0, renders 'main (dirty)', and dispatches the exact DTO."""
        (status_workspace / "untracked.txt").write_text("scratch\n", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status"])

        assert result.exit_code == 0
        assert "main (dirty)" in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeStatusResult)
        assert result_dto.root_dir == status_workspace
        assert result_dto.is_initialized is True
        assert result_dto.git.is_git_repo is True
        assert result_dto.git.branch == "main"
        assert result_dto.git.is_dirty is True
        assert result_dto.git.uncommitted_files == 1
        assert result_dto.config.status == ConfigLoadStatus.OK
        assert result_dto.config.config_path == status_workspace / ".worktree" / "config.json"
        assert result_dto.config.is_valid is True
        assert result_dto.catalog.exists is False
        assert result_dto.catalog.total_items == 0
        assert result_dto.database.exists is True
        assert result_dto.database.db_path == resolve_db_path(resolve_global_paths(None))
        assert result_dto.database.is_accessible is True
        assert result_dto.sandboxes.active_sandboxes == 0
        assert result_dto.sandboxes.max_active_sandboxes == 3
        assert result_dto.warnings == [
            _BRANCH_WARNING,
            "Working tree has 1 uncommitted change(s).",
            _AGENT_MODEL_WARNING,
        ]

    def test_status_cli_renders_json(self, cli_runner: CliRunner, status_workspace: Path) -> None:
        """wt status --format json on a clean git worktree emits the literal StatusView payload."""
        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorktreeStatusResult",
            "payload": {
                "health": "ok",
                "root_dir": str(status_workspace),
                "project_name": "workspace",
                "config_status": "ok",
                "config_path_relative": ".worktree/config.json",
                "git_branch": "main",
                "git_is_dirty": False,
                "uncommitted_files": 0,
                "agent_model": None,
                "active_sandboxes": 0,
                "max_active_sandboxes": 3,
                "valid_catalog_items": 0,
                "total_catalog_items": 0,
                "total_runs": 0,
                "errors": [],
                "warnings": [
                    "Active branch is 'main'. Automated workflows on primary branches are discouraged.",
                    "Agent model is not configured (agent.model is null).",
                ],
                "remediations": [],
            },
        }

    def test_status_cli_uninitialized_git_repo_reports_not_initialized_unchanged(
        self, cli_runner: CliRunner, git_repo: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt status: git repo with no .worktree/ still reports is_initialized=False and writes no project.json/config.json — lazy init does not extend to status."""
        result = cli_runner.invoke(app, ["-p", str(git_repo), "status"])

        assert result.exit_code == 0
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeStatusResult)
        assert result_dto.is_initialized is False
        assert not (git_repo / ".worktree" / "project.json").exists()
        assert not (git_repo / ".worktree" / "config.json").exists()
