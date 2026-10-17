"""Single-tier CLI integration tests for dovo status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.status.models import (
    DovoStatusResult,
)


class StatusCliIntegrationTests:
    """Typer runner integration tests for dovo status."""

    def test_status_cli_clean_worktree_exits_zero(
        self, cli_runner: CliRunner, status_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status"])

        assert result.exit_code == 0
        assert "main" in result.stdout
        assert "(dirty)" not in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, DovoStatusResult)
        assert result_dto.root_dir == status_workspace
        assert result_dto.git.is_dirty is False

    def test_status_cli_dirty_worktree_shows_dirty_branch_exits_zero(
        self, cli_runner: CliRunner, status_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        (status_workspace / "untracked.txt").write_text("scratch\n", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status"])

        assert result.exit_code == 0
        assert "main (dirty)" in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, DovoStatusResult)
        assert result_dto.root_dir == status_workspace
        assert result_dto.git.is_dirty is True
        assert result_dto.git.uncommitted_files == 1

    def test_status_cli_renders_json(self, cli_runner: CliRunner, status_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "DovoStatusResult"
        assert event["payload"]["health"] == "ok"
        assert event["payload"]["root_dir"] == str(status_workspace)
