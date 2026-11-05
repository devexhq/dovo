"""Single-tier CLI integration tests for dovo status."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import WorkspaceLock
from dovo.core.db import SessionStatus
from dovo.core.git import GitError
from dovo.core.status.models import (
    DovoStatusResult,
)
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.exit_codes import OUTPUT_FORMATS
from tests.harness.fakes import FakeGitRunner, use_fake_git
from tests.harness.lock import lock_timeout_reported, raise_lock_timeout
from tests.harness.sessions import fail_reconciliation_list
from tests.harness.workspace_paths import initialized_workspace_db


def _leave_healthy(_workspace: Path) -> None:
    return None


def _break_config(workspace: Path) -> None:
    (workspace / ".dovo" / "config.json").write_text("{not valid json", encoding="utf-8")


def _remove_config(workspace: Path) -> None:
    (workspace / ".dovo" / "config.json").unlink()


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
        assert event["event_type"] == "status"
        assert event["payload"]["ok"] is True
        assert event["payload"]["error_code"] is None
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["root_dir"] == str(status_workspace)

    def test_status_cli_json_broken_config_reports_config_error_code_and_exits_zero(
        self, cli_runner: CliRunner, status_workspace: Path
    ) -> None:
        """Exit 0 with ok true, status degraded, config_error_code CONFIG_JSON_INVALID and no config_status key."""
        (status_workspace / ".dovo" / "config.json").write_text("{not valid json", encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert payload["ok"] is True
        assert payload["error_code"] is None
        assert payload["status"] == "degraded"
        assert payload["config_error_code"] == "CONFIG_JSON_INVALID"
        assert "config_status" not in payload

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        "arrange",
        [
            pytest.param(_leave_healthy, id="healthy"),
            pytest.param(_break_config, id="degraded"),
            pytest.param(_remove_config, id="missing_config"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        status_workspace: Path,
        dispatch_spy: list[object],
        output_format: str,
        arrange: Callable[[Path], None],
    ) -> None:
        """Exit 0 with no error_code whatever health status reports, read from the dispatched result."""
        arrange(status_workspace)

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", output_format])

        assert result.exit_code == 0, result.output
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, DovoStatusResult)
        assert result_dto.error_code is None

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
        assert event["event_type"] == "status"
        assert not any("Details logged" in warning for warning in event["payload"]["warnings"])
        assert event["payload"]["diagnostics"] == [
            {"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}
        ]

    def test_status_cli_json_reports_reconciled_stale_session(
        self, cli_runner: CliRunner, status_workspace: Path
    ) -> None:
        """With a running row "session-stale" with no pid, exit 0, emit payload warnings[0] "Marked 1 interrupted session failed (session_id: session-stale)." and leave the row failed."""
        sessions_db = initialized_workspace_db(status_workspace).sessions
        sessions_db.create("session-stale", "blueprint", "blueprint")

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["payload"]["warnings"][0] == "Marked 1 interrupted session failed (session_id: session-stale)."
        stored = sessions_db.get("session-stale")
        assert stored is not None
        assert stored.status is SessionStatus.FAILED

    def test_status_cli_json_under_lock_timeout_exits_one(
        self, cli_runner: CliRunner, status_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with the lock timeout reported for `status --format json`."""
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 1
        assert lock_timeout_reported(result.stdout)

    def test_status_cli_json_reports_unknown_branch_when_git_is_missing(
        self,
        cli_runner: CliRunner,
        status_workspace: Path,
        dispatch_spy: list[object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 for `status --format json` with use_fake_git raising a GIT_BINARY_MISSING GitError for rev-parse --is-inside-work-tree, dispatch a result with git.branch "unknown" and git.is_git_repo False, and emit payload git_branch null."""
        fake = FakeGitRunner(
            {("rev-parse", "--is-inside-work-tree"): GitError(ErrorCode.GIT_BINARY_MISSING, "git not found")}
        )
        use_fake_git(monkeypatch, fake)

        result = cli_runner.invoke(app, ["-p", str(status_workspace), "status", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["git_branch"] is None
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, DovoStatusResult)
        assert result_dto.git.branch == "unknown"
        assert result_dto.git.is_git_repo is False
