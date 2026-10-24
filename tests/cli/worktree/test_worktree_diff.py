"""Single-tier CLI integration tests for dovo worktree diff."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeDiffResult, WorktreeDiffStatus, WorktreeSession
from dovo.core.worktree.worktree import Worktree
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.git_index import snapshot_index
from tests.harness.workspace_paths import initialized_workspace_paths


def _create_worktree_with_committed_change(cli_workspace: Path) -> WorktreeSession:
    """Create a worktree and commit one file change inside its worktree."""
    create_result = Worktree(
        paths=initialized_workspace_paths(cli_workspace), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
    ).create(name="diff-me")
    assert create_result.session is not None
    session = create_result.session

    (session.worktree_path / "target.py").write_text("changed\n", encoding="utf-8")
    GitRunner().run(["add", "-A"], path=session.worktree_path)
    GitRunner().run(["commit", "-m", "Worktree change to target.py"], path=session.worktree_path)

    return session


def _assert_dispatches_ok_diff(dispatch_spy: list[WorktreeDiffResult], session_id: str, *, stat: bool) -> None:
    """Assert the spy captured exactly one OK WorktreeDiffResult for target.py, with a diffstat only when stat."""
    assert len(dispatch_spy) == 1
    captured = dispatch_spy[0]
    assert captured.status == WorktreeDiffStatus.OK
    assert captured.worktree_id == session_id
    assert ("target.py" in captured.stat_text) is stat


class WorktreeDiffCliIntegrationTests:
    """Typer runner integration tests for dovo worktree diff."""

    def test_worktree_diff_cli_renders_unified_diff_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDiffResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "diff", session.session_id])

        assert result.exit_code == 0
        assert "diff --git" in result.stdout
        assert "+changed" in result.stdout
        _assert_dispatches_ok_diff(dispatch_spy, session.session_id, stat=False)

    def test_worktree_diff_cli_stat_flag_renders_diffstat(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDiffResult]
    ) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "diff", session.session_id, "--stat"])

        assert result.exit_code == 0
        assert "target.py" in result.stdout
        assert "diff --git" not in result.stdout
        _assert_dispatches_ok_diff(dispatch_spy, session.session_id, stat=True)

    def test_worktree_diff_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[WorktreeDiffResult]
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "diff", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        captured = dispatch_spy[0]
        assert captured.status == WorktreeDiffStatus.NOT_FOUND
        assert captured.worktree_id == "missing-id"
        assert captured.errors == ["Worktree 'missing-id' not found."]

    def test_worktree_diff_cli_renders_json(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "worktree", "diff", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "WorktreeDiffResult"
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["files_changed"] == ["target.py"]

    def test_worktree_diff_cli_keeps_untracked_file_untracked(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session = _create_worktree_with_committed_change(cli_workspace)
        (session.worktree_path / "new.txt").write_text("untracked\n", encoding="utf-8")
        before = snapshot_index(session.worktree_path)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "worktree", "diff", session.session_id])

        assert result.exit_code == 0
        assert "new.txt" in result.stdout
        assert snapshot_index(session.worktree_path) == before
