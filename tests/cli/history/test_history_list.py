"""Single-tier CLI integration tests for dovo history / dovo history list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _db_for(workspace: Path) -> DovoDb:
    """Construct a DovoDb bound to workspace's resolved database file and project id."""
    paths = _paths_for(workspace)
    return DovoDb(database_file=paths.database_file, project_id=paths.project_id)


class HistoryListCliIntegrationTests:
    """Typer runner integration tests for dovo history / dovo history list."""

    def test_history_cli_bare_lists_recent_runs_exits_zero(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """dovo history: bare invocation lists seeded COMPLETED and FAILED sessions, exit 0, both session IDs in stdout."""
        db = _db_for(history_workspace)
        db.sessions.create(
            session_id="session-completed",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
        )
        db.sessions.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history"])

        assert result.exit_code == 0
        assert "session-completed" in result.stdout
        assert "session-failed" in result.stdout

    def test_history_list_cli_json_emits_literal_wire_payload(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """dovo history list --format json: stdout equals the literal HistoryListResult envelope for the two seeded sessions."""
        db = _db_for(history_workspace)
        db.sessions.create(
            session_id="session-completed",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
        )
        db.sessions.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "list", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["event_type"] == "HistoryListResult"
        payload = data["payload"]
        sessions = payload["sessions"]
        assert len(sessions) == 2
        for r in sessions:
            assert isinstance(r["started_at"], str)
            r["started_at"] = "<timestamp>"
        assert payload == {
            "status": "ok",
            "sessions": [
                {
                    "session_id": "session-failed",
                    "blueprint_name": "task-b",
                    "status": "failed",
                    "branch_name": None,
                    "started_at": "<timestamp>",
                    "completed_at": None,
                    "duration_seconds": None,
                    "error_message": None,
                },
                {
                    "session_id": "session-completed",
                    "blueprint_name": "task-a",
                    "status": "completed",
                    "branch_name": None,
                    "started_at": "<timestamp>",
                    "completed_at": None,
                    "duration_seconds": None,
                    "error_message": None,
                },
            ],
            "total_sessions": 2,
            "errors": [],
            "warnings": [],
            "fixes": [],
        }

    @pytest.mark.parametrize(
        ("names", "expected_text"),
        [
            pytest.param(["DB_PIN"], "[REDACTED:DB_PIN]", id="listed"),
            pytest.param([], "1234", id="unlisted"),
        ],
    )
    def test_history_list_masks_name_listed_in_config(
        self,
        cli_runner: CliRunner,
        history_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        names: list[str],
        expected_text: str,
    ) -> None:
        """[tier-3/integration] dovo history list: config environment.sensitive_variables ["DB_PIN"], DB_PIN="1234", row error_message "1234" prints "[REDACTED:DB_PIN]", exit 0; with the name removed from config it prints "1234"."""
        monkeypatch.setenv("DB_PIN", "1234")
        paths = _paths_for(history_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        db.sessions.create(
            session_id="session-pin", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        db.sessions.update_status("session-pin", SessionStatus.FAILED, error_message="1234")
        config = json.loads(paths.config_file.read_text(encoding="utf-8"))
        config["environment"] = {"sensitive_variables": names}
        Filesystem.atomic_write_json(paths.config_file, config)

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "list", "--format", "json"])

        assert result.exit_code == 0
        sessions = json.loads(result.stdout)["payload"]["sessions"]
        assert [row["error_message"] for row in sessions] == [expected_text]
