"""Single-tier CLI integration tests for dovo worktree delete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeDeleteResult, WorktreeDeleteStatus, WorktreeSession
from dovo.core.worktree.worktree import Worktree
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
from tests.harness.fakes import FakeGitRunner, use_fake_git
from tests.harness.workspace_paths import initialized_workspace_paths


def _create_worktree(cli_workspace: Path) -> WorktreeSession:
    """Create a fresh worktree via the entrypoint for one delete test."""
    create_result = Worktree(paths=initialized_workspace_paths(cli_workspace), git=GitRunner()).create(name="delete-me")
    assert create_result.session is not None
    return create_result.session


class WorktreeDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo worktree delete."""

    def test_worktree_delete_cli_declined_confirmation_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDeleteResult]
    ) -> None:
        session = _create_worktree(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "delete", session.session_id], input="n\n"
        )

        assert result.exit_code == 1
        assert "Aborted." in result.stdout
        assert session.worktree_path.is_dir()
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].status == WorktreeDeleteStatus.ABORTED
        assert dispatch_spy[0].worktree_id == session.session_id
        assert not dispatch_spy[0].deleted

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
        assert dispatch_spy[0].status == WorktreeDeleteStatus.DELETED
        assert dispatch_spy[0].worktree_id == session.session_id
        assert dispatch_spy[0].deleted

    def test_worktree_delete_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDeleteResult]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "delete", "missing-id", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].status == WorktreeDeleteStatus.NOT_FOUND
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
        assert event["payload"]["status"] == "deleted"
        assert event["payload"]["worktree_id"] == session.session_id

    def test_worktree_delete_cli_cleanup_failure_json_carries_warning_with_hint(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 for --force --format json with git branch -D raising RuntimeError("boom") after _create_worktree, emit status "deleted" and payload warnings == [f"Failed to delete branch '{branch}': boom\nDetails logged: {path} (event exc_3f9a1c07b2de)"], and write a record with command "worktree" and operation worktree.cleanup."""
        session = _create_worktree(cli_workspace)
        use_fake_git(monkeypatch, FakeGitRunner({("branch", "-D"): RuntimeError("boom")}))

        result = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "worktree", "delete", session.session_id, "--force", "--format", "json"],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert payload["status"] == "deleted"
        assert payload["warnings"] == [
            f"Failed to delete branch '{session.target_branch}': boom{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["command"] == "worktree"
        assert event["operation"] == "worktree.cleanup"
