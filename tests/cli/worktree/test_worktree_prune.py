"""Single-tier CLI integration tests for dovo worktree prune."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.worktree.models import WorktreePruneStatus


class WorktreePruneCliIntegrationTests:
    """Typer runner integration tests for dovo worktree prune."""

    @pytest.mark.parametrize(
        ("flag", "dry_run", "force"),
        [
            pytest.param("--dry-run", True, False, id="dry-run"),
            pytest.param("--force", False, True, id="force"),
        ],
    )
    def test_worktree_prune_cli_flag_wired_reports_nothing_exits_zero(
        self,
        cli_runner: CliRunner,
        worktree_workspace: Path,
        dispatch_spy: list[Any],
        flag: str,
        dry_run: bool,
        force: bool,
    ) -> None:
        """dovo worktree prune --dry-run/--force on a healthy workspace exits 0, reports no stale worktrees, and dispatches the exact empty DTO."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "prune", flag])

        assert result.exit_code == 0
        assert "No stale worktrees found." in result.stdout
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == WorktreePruneStatus.OK
        assert payload.dry_run == dry_run
        assert payload.force == force
        assert len(payload.items) == 0
        assert len(payload.errors) == 0

    def test_worktree_prune_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree prune --dry-run --format json emits a WorktreePruneResult envelope wrapping the empty view."""
        result = cli_runner.invoke(
            app, ["-p", str(worktree_workspace), "worktree", "prune", "--dry-run", "--format", "json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorktreePruneResult",
            "payload": {
                "status": "ok",
                "dry_run": True,
                "force": False,
                "items": [],
                "pruned_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
