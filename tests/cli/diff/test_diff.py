"""Single-tier CLI integration tests for dovo diff."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.engine.writer import get_session_dir, write_session_diff


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


_PATCH_TEXT = (
    "diff --git a/file.txt b/file.txt\n"
    "index 111..222 100644\n"
    "--- a/file.txt\n"
    "+++ b/file.txt\n"
    "@@ -1 +1 @@\n"
    "-old line\n"
    "+new line\n"
)


def _write_session_diff(diff_workspace: Path, session_id: str) -> Path:
    """Write a real unified-diff patch file directly under .dovo/sessions/<id>/diff.patch.

    diff_workspace is built via WorkspaceBuilder.with_database(), which now persists a project
    identity; remove it so session storage resolves to this local path rather than global
    per-project storage (see resolve_workspace_paths).
    """
    (diff_workspace / ".dovo" / "project.json").unlink(missing_ok=True)
    session_dir = diff_workspace / ".dovo" / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    patch_path = session_dir / "diff.patch"
    patch_path.write_text(_PATCH_TEXT, encoding="utf-8")
    return patch_path


def _write_global_session_diff(diff_workspace: Path, session_id: str) -> Path:
    """Persist an identified project's patch in selected global session storage."""
    identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    save_project_identity(diff_workspace / ".dovo" / "project.json", identity)
    session_dir = get_session_dir(_paths_for(diff_workspace), session_id)
    return write_session_diff(session_dir, _PATCH_TEXT)


class DiffCliIntegrationTests:
    """Typer runner integration tests for dovo diff."""

    def test_diff_cli_known_session_renders_patch_exits_zero(self, cli_runner: CliRunner, diff_workspace: Path) -> None:
        """dovo diff <session_id>: reads diff.patch off disk, exit 0, patch content in stdout."""
        _write_session_diff(diff_workspace, "sess-diff-1")

        result = cli_runner.invoke(app, ["-p", str(diff_workspace), "diff", "sess-diff-1"])

        assert result.exit_code == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "-old line" in result.stdout
        assert "+new line" in result.stdout

    def test_diff_cli_unknown_session_exits_one(self, cli_runner: CliRunner, diff_workspace: Path) -> None:
        """dovo diff <unknown-id>: exit 1, 'not found under .dovo/sessions' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(diff_workspace), "diff", "unknown-session"])

        assert result.exit_code == 1
        assert "not found under .dovo/sessions" in result.stdout

    def test_diff_cli_json_emits_literal_wire_payload(self, cli_runner: CliRunner, diff_workspace: Path) -> None:
        """dovo diff <session_id> --format json: stdout equals the literal DiffResultView envelope."""
        patch_path = _write_session_diff(diff_workspace, "sess-diff-1")

        result = cli_runner.invoke(app, ["-p", str(diff_workspace), "diff", "sess-diff-1", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "DiffResult",
            "payload": {
                "status": "ok",
                "session_id": "sess-diff-1",
                "artifact_path": str(patch_path),
                "relative_path": str(patch_path),
                "diff_text": _PATCH_TEXT,
                "raw": False,
                "full": False,
                "max_lines": 500,
                "total_lines": 7,
                "truncated": False,
                "truncated_lines": 0,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_diff_cli_with_project_identity_reads_global_patch_and_exits_zero(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, diff_workspace: Path, tmp_path: Path
    ) -> None:
        """An identified project presents the patch persisted in global storage."""
        global_root = tmp_path / "global"
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        _write_global_session_diff(diff_workspace, "session-626")

        result = cli_runner.invoke(app, ["-p", str(diff_workspace), "diff", "session-626"])

        assert result.exit_code == 0
        assert "-old line" in result.stdout
        assert "+new line" in result.stdout

    def test_diff_cli_with_project_identity_json_emits_global_artifact_path(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch, diff_workspace: Path, tmp_path: Path
    ) -> None:
        """JSON output exposes the exact global patch path and patch body."""
        global_root = tmp_path / "global"
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        patch_path = _write_global_session_diff(diff_workspace, "session-626")

        result = cli_runner.invoke(app, ["-p", str(diff_workspace), "diff", "session-626", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "DiffResult",
            "payload": {
                "status": "ok",
                "session_id": "session-626",
                "artifact_path": str(patch_path),
                "relative_path": str(patch_path),
                "diff_text": _PATCH_TEXT,
                "raw": False,
                "full": False,
                "max_lines": 500,
                "total_lines": 7,
                "truncated": False,
                "truncated_lines": 0,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }
