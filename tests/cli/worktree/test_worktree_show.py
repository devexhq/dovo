"""Single-tier CLI integration tests for dovo worktree show."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeShowResult
from dovo.core.worktree.worktree import Worktree
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.workspace_paths import initialized_workspace_paths


class WorktreeShowCliIntegrationTests:
    """Typer runner integration tests for dovo worktree show."""

    def test_worktree_show_cli_existing_worktree_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        create_result = Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "show", session.session_id])

        assert result.exit_code == 0
        assert session.session_id in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeShowResult)
        assert result_dto.ok
        assert result_dto.worktree is not None
        assert result_dto.worktree.id == session.session_id
        assert result_dto.worktree.name == "show-me"

    def test_worktree_show_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        result_dto = dispatch_spy[0]
        assert isinstance(result_dto, WorktreeShowResult)
        assert result_dto.error_code == "WORKTREE_NOT_FOUND"
        assert result_dto.errors == ["Worktree 'missing-id' not found."]
        assert "Fix:" in result.stdout
        assert "- Run 'dovo worktree list' to see known worktrees" in result.stdout

    def test_worktree_show_cli_missing_worktree_json_carries_remediation(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Publish the core not-found remediation as a message/command object and no fixes entry."""
        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "show", "missing-id", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "WORKTREE_NOT_FOUND"
        assert payload["diagnostics"] == []
        assert "status" not in payload
        assert payload["remediations"] == [
            {"message": "Run 'dovo worktree list' to see known worktrees", "command": "dovo worktree list"}
        ]
        assert "fixes" not in payload

    def test_worktree_show_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        create_result = Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="show-me")
        assert create_result.session is not None
        session = create_result.session

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "show", session.session_id, "--format", "json"],
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeShowResult"
        assert event["payload"]["error_code"] is None
        assert event["payload"]["worktree"]["id"] == session.session_id

    def test_worktree_show_cli_unexpected_failure_json_reports_internal_error(
        self, cli_runner: CliRunner, cli_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 and emit payload error_code "INTERNAL_ERROR", errors == ["Unexpected error during worktree.show: database is locked"] and diagnostics == [RECORD_JSON] when update_status raises sqlite3.OperationalError("database is locked") for a stale active row."""
        create_result = Worktree(
            paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(name="stale")
        assert create_result.session is not None
        session = create_result.session
        shutil.rmtree(session.worktree_path)

        def _locked(*args: object, **kwargs: object) -> NoReturn:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(WorktreesRepository, "update_status", _locked)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "show", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["errors"] == ["Unexpected error during worktree.show: database is locked"]
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
