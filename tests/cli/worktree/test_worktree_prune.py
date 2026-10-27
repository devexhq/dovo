"""Single-tier CLI integration tests for dovo worktree prune."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.exceptions import git_command_error
from dovo.core.worktree.models import WorktreePruneStatus
from dovo.core.worktree.services.detector import WorktreeDetector
from tests.harness.fakes import FakeGitRunner, use_fake_git


def _raise_type_error(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for detection failing with an unexpected TypeError("boom")."""
    raise TypeError("boom")


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
        cli_workspace: Path,
        dispatch_spy: list[Any],
        flag: str,
        dry_run: bool,
        force: bool,
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "prune", flag])

        assert result.exit_code == 0
        assert "No stale worktrees found." in result.stdout
        assert len(dispatch_spy) == 1

        payload = dispatch_spy[0]
        assert payload.status == WorktreePruneStatus.OK
        assert payload.dry_run == dry_run
        assert payload.force == force
        assert len(payload.items) == 0
        assert len(payload.errors) == 0

    def test_worktree_prune_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "prune", "--dry-run", "--format", "json"]
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
                "error_code": None,
                "errors": [],
                "warnings": [],
                "remediations": [],
                "diagnostics": [],
            },
        }

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param("partial_failure", id="partial_failure"),
            pytest.param("unexpected", id="unexpected"),
            pytest.param("git_failed", id="git_failed"),
        ],
    )
    def test_worktree_prune_cli_failure_json(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        case: str,
    ) -> None:
        """Exit 1 for --format json. partial_failure (git branch -D raising RuntimeError("boom") via use_fake_git on a stale dovo/dovo_x branch) emits status "partial_failure", error_code "WORKTREE_PRUNE_ITEMS_FAILED", errors ["Failed to delete branch 'dovo/dovo_x': boom"] with no hint and one diagnostics entry. unexpected (WorktreeDetector.detect raising TypeError("boom")) emits status null, error_code "INTERNAL_ERROR", errors ["Unexpected error during worktree.prune: boom"] and diagnostics [{"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}]. git_failed (git worktree list raising a GIT_FAILED GitError) emits status null, error_code "GIT_FAILED", its message as the one error, no items and no diagnostics."""
        if case == "partial_failure":
            subprocess.run(["git", "branch", "dovo/dovo_x"], cwd=cli_workspace, check=True, capture_output=True)
            use_fake_git(monkeypatch, FakeGitRunner({("branch", "-D"): RuntimeError("boom")}))
        elif case == "git_failed":
            failure = git_command_error(["worktree", "list"], 1, "", "fatal error")
            use_fake_git(monkeypatch, FakeGitRunner({("worktree", "list"): failure}))
        else:
            monkeypatch.setattr(WorktreeDetector, "detect", _raise_type_error)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "prune", "--format", "json"])

        assert result.exit_code == 1
        payload = json.loads(result.stdout)["payload"]
        if case == "partial_failure":
            assert payload["status"] == "partial_failure"
            assert payload["error_code"] == "WORKTREE_PRUNE_ITEMS_FAILED"
            assert payload["errors"] == ["Failed to delete branch 'dovo/dovo_x': boom"]
            assert payload["diagnostics"] == [
                {"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}
            ]
        elif case == "git_failed":
            assert payload["status"] is None
            assert payload["error_code"] == "GIT_FAILED"
            assert payload["errors"] == ["Git execution failed ('git worktree list'): fatal error"]
            assert payload["items"] == []
            assert payload["diagnostics"] == []
        else:
            assert payload["status"] is None
            assert payload["error_code"] == "INTERNAL_ERROR"
            assert payload["errors"] == ["Unexpected error during worktree.prune: boom"]
            assert payload["items"] == []
            assert payload["diagnostics"] == [
                {"event_id": "exc_3f9a1c07b2de", "log_path": str(frozen_diagnostic_clock)}
            ]
