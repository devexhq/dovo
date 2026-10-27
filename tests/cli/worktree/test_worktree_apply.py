"""Single-tier CLI integration tests for dovo worktree apply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.error_codes import ErrorCode
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeApplyResult, WorktreeApplyStrategy, WorktreeSession
from dovo.core.worktree.worktree import Worktree
from tests.harness.diagnostic_log import read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id, use_fake_git
from tests.harness.workspace_paths import initialized_workspace_paths


def _create_worktree_with_committed_change(cli_workspace: Path) -> WorktreeSession:
    """Create a worktree and commit one file change inside its worktree."""
    create_result = Worktree(
        paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
    ).create(name="apply-me")
    assert create_result.session is not None
    session = create_result.session

    (session.worktree_path / "target.py").write_text("changed\n", encoding="utf-8")
    GitRunner().run(["add", "-A"], path=session.worktree_path)
    GitRunner().run(["commit", "-m", "Worktree change to target.py"], path=session.worktree_path)

    return session


class WorktreeApplyCliIntegrationTests:
    """Typer runner integration tests for dovo worktree apply."""

    def test_worktree_apply_cli_patch_strategy_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id])

        assert result.exit_code == 0
        assert "Applied worktree" in result.stdout
        assert (cli_workspace / "target.py").read_text(encoding="utf-8") == "changed\n"
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].ok
        assert dispatch_spy[0].worktree_id == session.session_id
        assert dispatch_spy[0].strategy == WorktreeApplyStrategy.PATCH

    def test_worktree_apply_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "apply", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].error_code == "WORKTREE_NOT_FOUND"
        assert dispatch_spy[0].worktree_id == "missing-id"
        assert dispatch_spy[0].errors == ["Worktree 'missing-id' not found."]

    def test_worktree_apply_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeApplyResult"
        assert "status" not in event["payload"]
        assert event["payload"]["error_code"] is None
        assert event["payload"]["touched_files"] == ["target.py"]

    def test_worktree_apply_cli_squash_allow_dirty_keeps_unrelated_changes(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)
        (cli_workspace / "scratch.txt").write_text("scratch\n", encoding="utf-8")

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "worktree",
                "apply",
                session.session_id,
                "--strategy",
                "squash",
                "--allow-dirty",
            ],
        )

        assert result.exit_code == 0
        commit_sha = GitRunner().rev_parse(cli_workspace, rev="HEAD")
        assert f"Applied worktree {session.session_id} to workspace (squash)" in result.stdout
        assert f"• Commit: {commit_sha}" in result.stdout
        assert dispatch_spy[0].commit_sha == commit_sha
        committed = GitRunner().run(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], path=cli_workspace)
        assert committed == "target.py"
        assert GitRunner().status_porcelain(cli_workspace) == ["?? scratch.txt"]

    def test_worktree_apply_cli_squash_allow_dirty_overlap_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)
        (cli_workspace / "target.py").write_text("user file\n", encoding="utf-8")

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "worktree",
                "apply",
                session.session_id,
                "--strategy",
                "squash",
                "--allow-dirty",
            ],
        )

        assert result.exit_code == 1
        assert "target.py" in result.stdout
        assert len(dispatch_spy) == 1
        assert dispatch_spy[0].error_code == "GIT_DIRTY_WORKTREE"
        assert dispatch_spy[0].errors == [
            f"Cannot squash worktree {session.session_id}: the main repository has uncommitted changes in files the "
            "worktree also changes.\nOverlapping files:\n  • target.py"
        ]
        assert (cli_workspace / "target.py").read_text(encoding="utf-8") == "user file\n"

    def test_worktree_apply_cli_unexpected_failure_json_reports_internal_error(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 1 for --format json with diff_work_tree raising RuntimeError, emit INTERNAL_ERROR with the unexpected-error message and one diagnostics entry, and write a record with command "worktree" and operation worktree.apply."""
        session = _create_worktree_with_committed_change(cli_workspace)

        def failing_diff(*args: object, **kwargs: object) -> NoReturn:
            raise RuntimeError("boom")

        monkeypatch.setattr("dovo.core.worktree.services.patch.diff_work_tree", failing_diff)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["errors"] == ["Unexpected error during worktree.apply: boom"]
        assert payload["diagnostics"] == [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["command"] == "worktree"
        assert event["operation"] == "worktree.apply"

    def test_worktree_apply_cli_already_merged_json_exits_one(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)
        paths = initialized_workspace_paths(cli_workspace)
        WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).update_status(
            session.session_id, WorktreeStatus.MERGED
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "WORKTREE_ALREADY_MERGED"
        assert payload["errors"] == [f"Worktree '{session.session_id}' is already merged."]
        assert payload["warnings"] == []

    def test_worktree_apply_cli_conflict_json_lists_conflicting_files(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Exit 1 for --format json when the main workspace changed target.py too, emitting WORKTREE_APPLY_CONFLICT, the conflict message and conflicting_files == ["target.py"]."""
        session = _create_worktree_with_committed_change(cli_workspace)
        (cli_workspace / "target.py").write_text("main change\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=cli_workspace)
        GitRunner().run(["commit", "-m", "Main change to target.py"], path=cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "WORKTREE_APPLY_CONFLICT"
        assert payload["errors"] == [
            f"Cannot apply worktree {session.session_id}: conflicts detected.\nConflicting files:\n  • target.py"
        ]
        assert payload["conflicting_files"] == ["target.py"]
        assert payload["touched_files"] == []

    def test_worktree_apply_cli_git_timeout_json_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)
        detail = "Git timed out after 30s ('git apply --check')"
        use_fake_git(monkeypatch, FakeGitRunner({("apply", "--check"): GitError(ErrorCode.GIT_TIMEOUT, detail)}))

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        assert payload["error_code"] == "GIT_TIMEOUT"
        assert payload["errors"] == [
            f"Git timed out during conflict check for worktree '{session.session_id}': {detail}"
        ]

    def test_worktree_apply_cli_dry_run_leaves_workspace_unchanged(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)
        status_before = GitRunner().status_porcelain(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--dry-run"]
        )

        assert result.exit_code == 0
        assert dispatch_spy[0].ok
        assert dispatch_spy[0].warnings == ["Dry run validation succeeded. No files were modified."]
        assert GitRunner().status_porcelain(cli_workspace) == status_before

    def test_worktree_apply_cli_delete_removes_worktree(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeApplyResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "apply", session.session_id, "--delete"])

        assert result.exit_code == 0
        assert dispatch_spy[0].cleaned_up is True
        assert not session.worktree_path.exists()

    def test_worktree_apply_cli_message_sets_squash_commit_message(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "worktree",
                "apply",
                session.session_id,
                "--strategy",
                "squash",
                "--message",
                "feat: custom squash",
            ],
        )

        assert result.exit_code == 0
        assert GitRunner().run(["log", "-1", "--format=%s"], path=cli_workspace) == "feat: custom squash"
