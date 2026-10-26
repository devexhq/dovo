"""Single-tier CLI integration tests for dovo worktree delete."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import WorktreesRepository
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeDeleteResult, WorktreeSession
from dovo.core.worktree.worktree import Worktree
from tests.harness.diagnostic_log import read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id, use_fake_git
from tests.harness.workspace_paths import initialized_workspace_paths


def _worktree(cli_workspace: Path) -> Worktree:
    """Build the Worktree entrypoint over cli_workspace."""
    return Worktree(
        paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
    )


def _create_worktree(cli_workspace: Path) -> WorktreeSession:
    """Create a fresh worktree via the entrypoint for one delete test."""
    create_result = _worktree(cli_workspace).create(name="delete-me")
    assert create_result.session is not None
    return create_result.session


def _raise_boom(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a call that fails with an unexpected RuntimeError("boom")."""
    raise RuntimeError("boom")


def _raise_type_error(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a call that fails with an unexpected TypeError("boom")."""
    raise TypeError("boom")


class WorktreeDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo worktree delete."""

    def test_worktree_delete_cli_declined_confirmation_reports_cancelled_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDeleteResult]
    ) -> None:
        """Exit 0 with input "n", print "Deletion cancelled.", keep the directory."""
        session = _create_worktree(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "delete", session.session_id], input="n\n"
        )

        assert result.exit_code == 0
        assert "Deletion cancelled." in result.stdout
        assert session.worktree_path.is_dir()
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].cancelled is True
        assert dispatch_spy[0].worktree_id == session.session_id
        assert dispatch_spy[0].deleted is False

    def test_worktree_delete_cli_declined_json_reports_cancelled_payload(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Exit 0 with --format json and input "n", and emit the payload with error_code None, errors [], warnings [], remediations [], diagnostics [], worktree_id, deleted False, already_cleaned False, cancelled True."""
        session = _create_worktree(cli_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "delete", session.session_id, "--format", "json"],
            input="n\n",
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout[result.stdout.index("{") :])["payload"]
        assert payload["worktree"]["id"] == session.session_id
        assert {key: value for key, value in payload.items() if key != "worktree"} == {
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "worktree_id": session.session_id,
            "deleted": False,
            "already_cleaned": False,
            "cancelled": True,
        }
        assert session.worktree_path.is_dir()

    def test_worktree_delete_cli_already_cleaned_json_skips_prompt_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Exit 0 with no input for a cleaned row and emit already_cleaned True, deleted False, cancelled False, error_code None."""
        session = _create_worktree(cli_workspace)
        _worktree(cli_workspace).cleanup(session)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "delete", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert payload["already_cleaned"] is True
        assert payload["deleted"] is False
        assert payload["cancelled"] is False
        assert payload["error_code"] is None

    @pytest.mark.parametrize(
        ("extra_args", "invoke_input"),
        [
            pytest.param([], "y\n", id="confirmed-via-stdin"),
            pytest.param(["--force"], None, id="force-flag"),
        ],
    )
    def test_worktree_delete_cli_confirmed_or_forced_deletes_exits_zero(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        dispatch_spy: list[WorktreeDeleteResult],
        extra_args: list[str],
        invoke_input: str | None,
    ) -> None:
        session = _create_worktree(cli_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "delete", session.session_id, *extra_args],
            input=invoke_input,
        )

        assert result.exit_code == 0
        assert "Worktree deleted:" in result.stdout
        assert not session.worktree_path.exists()
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].worktree_id == session.session_id
        assert dispatch_spy[0].deleted

    def test_worktree_delete_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDeleteResult]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "delete", "missing-id", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].error_code == "WORKTREE_NOT_FOUND"
        assert dispatch_spy[0].worktree_id == "missing-id"
        assert dispatch_spy[0].errors == ["Worktree 'missing-id' not found."]

    def test_worktree_delete_cli_format_json_emits_result_event(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        session = _create_worktree(cli_workspace)

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "delete", session.session_id, "--force", "--format", "json"],
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeDeleteResult"
        assert event["payload"]["deleted"] is True
        assert "status" not in event["payload"]
        assert event["payload"]["worktree_id"] == session.session_id

    def test_worktree_delete_cli_unexpected_failure_json_reports_internal_error(
        self, cli_runner: CliRunner, cli_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with --force --format json when WorktreesRepository.get raises TypeError("boom"), emitting error_code "INTERNAL_ERROR", errors == ["Unexpected error during worktree.delete: boom"] and diagnostics == [RECORD_JSON]."""
        monkeypatch.setattr(WorktreesRepository, "get", _raise_type_error)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "delete", "dovo_any", "--force", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["errors"] == ["Unexpected error during worktree.delete: boom"]
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]

    @pytest.mark.parametrize("target", ["branch_delete", "update_status"])
    def test_worktree_delete_cli_cleanup_failure_json_lists_record_in_diagnostics(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
    ) -> None:
        """Exit 0 for --force --format json when the step raises RuntimeError("boom"), emit deleted True, error_code None, the step's unhinted warning and diagnostics == [RECORD_JSON], and write a record with command "worktree" and operation worktree.cleanup."""
        session = _create_worktree(cli_workspace)
        if target == "branch_delete":
            use_fake_git(monkeypatch, FakeGitRunner({("branch", "-D"): RuntimeError("boom")}))
            warning = f"Failed to delete branch '{session.target_branch}': boom"
        else:
            monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
            warning = f"Failed to update database status to 'cleaned' for worktree '{session.session_id}': boom"

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "delete", session.session_id, "--force", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert payload["deleted"] is True
        assert payload["error_code"] is None
        assert payload["warnings"] == [warning]
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["command"] == "worktree"
        assert event["operation"] == "worktree.cleanup"
