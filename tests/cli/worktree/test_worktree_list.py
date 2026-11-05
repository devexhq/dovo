"""Single-tier CLI integration tests for dovo worktree list."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeListResult
from dovo.core.worktree.worktree import Worktree
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.workspace_paths import initialized_workspace_paths


def _locked(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a repository call failing with sqlite3.OperationalError("database is locked")."""
    raise sqlite3.OperationalError("database is locked")


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
        assert payload.ok
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
        assert payload.ok
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
        assert payload.ok
        assert len(payload.worktrees) == 0

    def test_worktree_list_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorktreeListResult",
            "payload": {
                "ok": True,
                "error_code": None,
                "errors": [],
                "warnings": [],
                "remediations": [],
                "diagnostics": [],
                "worktrees": [],
            },
        }

    def test_worktree_list_cli_unexpected_failure_json_reports_internal_error(
        self, cli_runner: CliRunner, cli_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 and emit payload error_code "INTERNAL_ERROR", errors == ["Unexpected error during worktree.list: database is locked"], diagnostics == [RECORD_JSON] and no "status" key when reconcile_stale_active raises sqlite3.OperationalError("database is locked")."""

        monkeypatch.setattr(WorktreesRepository, "reconcile_stale_active", _locked)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["errors"] == ["Unexpected error during worktree.list: database is locked"]
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
        assert "status" not in payload

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("reconcile_stale_active", "exit_code", "error_code"),
        [
            pytest.param(WorktreesRepository.reconcile_stale_active, 0, None, id="readable_database"),
            pytest.param(_locked, 1, "INTERNAL_ERROR", id="locked_database"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        output_format: str,
        reconcile_stale_active: Callable[..., object],
        exit_code: int,
        error_code: str | None,
    ) -> None:
        monkeypatch.setattr(WorktreesRepository, "reconcile_stale_active", reconcile_stale_active)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "list", "--format", output_format])

        assert_exit_follows_result(result, output_format, exit_code, error_code)
