"""Single-tier CLI integration tests for dovo worktree diff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.facade import Worktree
from dovo.core.worktree.models import WorktreeDiffStatus, WorktreeSession
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _create_worktree_with_committed_change(worktree_workspace: Path) -> WorktreeSession:
    """Create a worktree and commit one file change inside its worktree."""
    create_result = Worktree(paths=_paths_for(worktree_workspace)).create(name="diff-me")
    assert create_result.session is not None
    session = create_result.session

    (session.worktree_path / "target.py").write_text("changed\n", encoding="utf-8")
    GitRunner.add_all(session.worktree_path)
    GitRunner.commit(session.worktree_path, "Worktree change to target.py")

    return session


def _assert_dispatches_ok_diff(dispatch_spy: list[Any], session_id: str, *, stat: bool) -> None:
    """Assert the spy captured exactly one OK WorktreeDiffResult for target.py, with a diffstat only when stat."""
    assert len(dispatch_spy) == 1
    captured = dispatch_spy[0]
    assert captured.status == WorktreeDiffStatus.OK
    assert captured.worktree_id == session_id
    assert "diff --git" in captured.diff_text
    assert ("target.py" in captured.stat_text) is stat
    assert captured.files_changed == ["target.py"]
    assert len(captured.errors) == 0


class WorktreeDiffCliIntegrationTests:
    """Typer runner integration tests for dovo worktree diff."""

    def test_worktree_diff_cli_renders_unified_diff_exits_zero(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree diff <id> renders the unified diff hunk for a committed change, exits 0, and dispatches the exact OK DTO."""
        session = _create_worktree_with_committed_change(worktree_workspace)

        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "diff", session.session_id])

        assert result.exit_code == 0
        assert "diff --git" in result.stdout
        assert "+changed" in result.stdout
        _assert_dispatches_ok_diff(dispatch_spy, session.session_id, stat=False)

    def test_worktree_diff_cli_stat_flag_renders_diffstat(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree diff <id> --stat renders the diffstat summary instead of the unified diff."""
        session = _create_worktree_with_committed_change(worktree_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(worktree_workspace), "worktree", "diff", session.session_id, "--stat"]
        )

        assert result.exit_code == 0
        assert "target.py" in result.stdout
        assert "diff --git" not in result.stdout
        _assert_dispatches_ok_diff(dispatch_spy, session.session_id, stat=True)

    def test_worktree_diff_cli_missing_worktree_exits_one(
        self, cli_runner: CliRunner, worktree_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo worktree diff on a missing id exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(worktree_workspace), "worktree", "diff", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        captured = dispatch_spy[0]
        assert captured.status == WorktreeDiffStatus.NOT_FOUND
        assert captured.worktree_id == "missing-id"
        assert captured.diff_text == ""
        assert captured.stat_text == ""
        assert len(captured.files_changed) == 0
        assert captured.errors == ["Worktree 'missing-id' not found."]

    def test_worktree_diff_cli_renders_json(self, cli_runner: CliRunner, worktree_workspace: Path) -> None:
        """dovo worktree diff <id> --format json emits a WorktreeDiffResult envelope."""
        session = _create_worktree_with_committed_change(worktree_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(worktree_workspace), "worktree", "diff", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        payload = actual_json["payload"]
        assert "target.py" in payload["diff_text"]
        payload["diff_text"] = "placeholder"

        assert actual_json == {
            "event_type": "WorktreeDiffResult",
            "payload": {
                "status": "ok",
                "worktree_id": session.session_id,
                "diff_text": "placeholder",
                "stat_text": "",
                "files_changed": ["target.py"],
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
