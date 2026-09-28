"""Single-tier CLI integration tests for wt sandbox diff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.sandbox.facade import Sandbox
from worktree.core.sandbox.models import SandboxDiffStatus, SandboxSession


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _create_sandbox_with_committed_change(sandbox_workspace: Path) -> SandboxSession:
    """Create a sandbox and commit one file change inside its worktree."""
    create_result = Sandbox(paths=_paths_for(sandbox_workspace)).create(name="diff-me")
    assert create_result.session is not None
    session = create_result.session

    (session.sandbox_path / "target.py").write_text("changed\n", encoding="utf-8")
    GitRunner.add_all(session.sandbox_path)
    GitRunner.commit(session.sandbox_path, "Sandbox change to target.py")

    return session


def _assert_dispatches_ok_diff(dispatch_spy: list[Any], session_id: str) -> None:
    """Assert the spy captured exactly one OK SandboxDiffResult for target.py."""
    assert len(dispatch_spy) == 1
    captured = dispatch_spy[0]
    assert "target.py" in captured.stat_text
    assert captured.status == SandboxDiffStatus.OK
    assert captured.sandbox_id == session_id
    assert isinstance(captured.diff_text, str) and len(captured.diff_text) > 0
    assert isinstance(captured.stat_text, str) and len(captured.stat_text) > 0
    assert captured.files_changed == ["target.py"]
    assert len(captured.errors) == 0


class SandboxDiffCliIntegrationTests:
    """Typer runner integration tests for wt sandbox diff."""

    def test_sandbox_diff_cli_renders_unified_diff_exits_zero(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox diff <id> renders the changed filename for a committed change, exits 0, and dispatches the exact OK DTO."""
        session = _create_sandbox_with_committed_change(sandbox_workspace)

        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "diff", session.session_id])

        assert result.exit_code == 0
        assert "target.py" in result.stdout
        _assert_dispatches_ok_diff(dispatch_spy, session.session_id)

    def test_sandbox_diff_cli_stat_flag_renders_diffstat(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox diff <id> --stat renders a diffstat summary, same as the default (stat_text is always populated and preferred by the formatter regardless of --stat; see 🚨 in the implementation report)."""
        session = _create_sandbox_with_committed_change(sandbox_workspace)

        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "diff", session.session_id, "--stat"])

        assert result.exit_code == 0
        assert "target.py" in result.stdout
        assert "diff --git" not in result.stdout
        _assert_dispatches_ok_diff(dispatch_spy, session.session_id)

    def test_sandbox_diff_cli_missing_sandbox_exits_one(
        self, cli_runner: CliRunner, sandbox_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt sandbox diff on a missing id exits 1, reports not found, and dispatches the exact NOT_FOUND DTO."""
        result = cli_runner.invoke(app, ["-p", str(sandbox_workspace), "sandbox", "diff", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert len(dispatch_spy) == 1
        captured = dispatch_spy[0]
        assert captured.status == SandboxDiffStatus.NOT_FOUND
        assert captured.sandbox_id == "missing-id"
        assert captured.diff_text == ""
        assert captured.stat_text == ""
        assert len(captured.files_changed) == 0
        assert captured.errors == ["Sandbox 'missing-id' not found."]

    def test_sandbox_diff_cli_renders_json(self, cli_runner: CliRunner, sandbox_workspace: Path) -> None:
        """wt sandbox diff <id> --format json emits a SandboxDiffResult envelope."""
        session = _create_sandbox_with_committed_change(sandbox_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(sandbox_workspace), "sandbox", "diff", session.session_id, "--format", "json"]
        )

        assert result.exit_code == 0
        actual_json = json.loads(result.stdout)
        payload = actual_json["payload"]
        assert "target.py" in payload["diff_text"]
        assert "target.py" in payload["stat_text"]
        payload["diff_text"] = "placeholder"
        payload["stat_text"] = "placeholder"

        assert actual_json == {
            "event_type": "SandboxDiffResult",
            "payload": {
                "status": "ok",
                "sandbox_id": session.session_id,
                "diff_text": "placeholder",
                "stat_text": "placeholder",
                "files_changed": ["target.py"],
                "error_code": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
