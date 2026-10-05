"""Single-tier CLI integration tests for dovo diff."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import DovoDb, SessionRecord, SessionStatus
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


def _seed_run(paths: WorkspacePaths, session_id: str, started_at: str | None = None) -> None:
    """Persist a COMPLETED session record for session_id, pinning started_at when given."""
    sessions = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions
    sessions.create(session_id=session_id, blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
    if started_at is None:
        return

    with sessions.session() as sql_session:
        record = sql_session.exec(select(SessionRecord).where(SessionRecord.session_id == session_id)).one()
        record.started_at = started_at
        sql_session.add(record)
        sql_session.commit()


def _write_session_diff(diff_workspace: Path, session_id: str, started_at: str | None = None) -> Path:
    """Persist a session record and a real unified-diff patch for session_id in the workspace's session storage."""
    paths = _paths_for(diff_workspace)
    _seed_run(paths, session_id, started_at)
    return write_session_diff(get_session_dir(paths, session_id), _PATCH_TEXT)


def _write_global_session_diff(diff_workspace: Path, session_id: str) -> Path:
    """Persist an identified project's session record and patch in selected global session storage."""
    identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    save_project_identity(diff_workspace / ".dovo" / "project.json", identity)
    paths = _paths_for(diff_workspace)
    _seed_run(paths, session_id)
    return write_session_diff(get_session_dir(paths, session_id), _PATCH_TEXT)


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

    def test_diff_cli_without_session_id_renders_latest_started_session(
        self, cli_runner: CliRunner, diff_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo diff (no id): two seeded session records where the earlier started_at has the newer directory mtime -> exit 0 and stdout contains the later started_at session's patch lines."""
        later_patch = _write_session_diff(diff_workspace, "later-run", started_at="2026-01-02 00:00:00")
        later_patch.write_text("diff --git a/later.txt b/later.txt\n-before\n+later-session-line\n", encoding="utf-8")
        earlier_patch = _write_session_diff(diff_workspace, "earlier-run", started_at="2026-01-01 00:00:00")
        os.utime(earlier_patch.parent, (4_000_000_000, 4_000_000_000))

        result = cli_runner.invoke(app, ["-p", str(diff_workspace), "diff"])

        assert result.exit_code == 0
        assert "+later-session-line" in result.stdout
        assert "+new line" not in result.stdout

    def test_diff_cli_raw_and_full_flags_bind_into_json_payload(
        self, cli_runner: CliRunner, diff_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo diff <id> --raw --full --format json: exit 0 and payload carries "raw": true, "full": true, "max_lines": 500."""
        _write_session_diff(diff_workspace, "sess-diff-1")

        result = cli_runner.invoke(
            app, ["-p", str(diff_workspace), "diff", "sess-diff-1", "--raw", "--full", "--format", "json"]
        )

        payload = json.loads(result.stdout)["payload"]
        assert result.exit_code == 0
        assert (payload["raw"], payload["full"], payload["max_lines"]) == (True, True, 500)

    def test_diff_cli_unknown_session_json_payload_keeps_default_display_options(
        self, cli_runner: CliRunner, diff_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo diff unknown --format json: exit 1 and payload has status "session_not_found", "raw": false, "full": false, "max_lines": null."""
        result = cli_runner.invoke(
            app, ["-p", str(diff_workspace), "diff", "unknown", "--raw", "--full", "--format", "json"]
        )

        payload = json.loads(result.stdout)["payload"]
        assert result.exit_code == 1
        assert payload["status"] == "session_not_found"
        assert (payload["raw"], payload["full"], payload["max_lines"]) == (False, False, None)
