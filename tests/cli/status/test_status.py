"""Single-tier CLI integration tests for dovo status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git import GitNotFoundError
from dovo.core.status.models import (
    DovoStatusResult,
)
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.fakes import FakeGitRunner, use_fake_git
from tests.harness.sessions import fail_reconciliation_list


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

    def test_status_cli_json_reports_reconciliation_record_in_diagnostics(
        self,
        cli_runner: CliRunner,
        status_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 and emit a DovoStatusResult payload with no warning for the failed reconciliation and diagnostics [RECORD_JSON]."""
        fail_reconciliation_list(monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "DovoStatusResult"
        assert not any("Details logged" in warning for warning in event["payload"]["warnings"])
        assert event["payload"]["diagnostics"] == [
            {"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}
        ]

    def test_status_cli_json_reports_unknown_branch_when_git_is_missing(
        self,
        cli_runner: CliRunner,
        status_workspace: Path,
        dispatch_spy: list[object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 for `status --format json` with use_fake_git raising GitNotFoundError for rev-parse --is-inside-work-tree, dispatch a result with git.branch "unknown" and git.is_git_repo False, and emit payload git_branch null."""
        fake = FakeGitRunner({("rev-parse", "--is-inside-work-tree"): GitNotFoundError("git not found")})
        use_fake_git(monkeypatch, fake)

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["git_branch"] is None
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, DovoStatusResult)
        assert result_dto.git.branch == "unknown"
        assert result_dto.git.is_git_repo is False
