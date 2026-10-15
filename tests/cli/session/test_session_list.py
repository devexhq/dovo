"""Single-tier CLI integration tests for dovo session / dovo session list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import DovoDb, SessionStatus
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _db_for(workspace: Path) -> DovoDb:
    """Construct a DovoDb bound to workspace's resolved database file and project id."""
    paths = _paths_for(workspace)
    return DovoDb(database_file=paths.database_file, project_id=paths.project_id)


class SessionListCliIntegrationTests:
    """Typer runner integration tests for dovo session / dovo session list."""

    def test_session_cli_bare_lists_recent_runs_exits_zero(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        db = _db_for(session_workspace)
        db.sessions.create(
            session_id="session-completed",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
        )
        db.sessions.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session"])

        assert result.exit_code == 0
        assert "session-completed" in result.stdout
        assert "session-failed" in result.stdout

    def test_session_list_cli_json_emits_literal_wire_payload(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        db = _db_for(session_workspace)
        db.sessions.create(
            session_id="session-completed",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
        )
        db.sessions.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "list", "--format", "json"])

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

    def test_bare_session_and_ls_alias_equal_session_list(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        db = _db_for(session_workspace)
        db.sessions.create(
            session_id="session-one", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        db.sessions.create(
            session_id="session-two", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.COMPLETED
        )

        listed = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "list"])
        bare = cli_runner.invoke(app, ["-p", str(session_workspace), "session"])
        aliased = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "ls"])

        assert (listed.exit_code, bare.exit_code, aliased.exit_code) == (0, 0, 0)
        assert "session-two" in listed.stdout
        assert bare.stdout == listed.stdout
        assert aliased.stdout == listed.stdout

    def test_bare_session_forwards_limit_status_and_format(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        db = _db_for(session_workspace)
        db.sessions.create(
            session_id="failed-old", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.FAILED
        )
        db.sessions.create(
            session_id="failed-new", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.FAILED
        )
        db.sessions.create(
            session_id="completed-last", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        bare = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "--status", "failed", "--limit", "1", "--format", "json"]
        )
        listed = cli_runner.invoke(
            app,
            ["-p", str(session_workspace), "session", "list", "--status", "failed", "--limit", "1", "--format", "json"],
        )

        assert bare.exit_code == 0
        payload = json.loads(bare.stdout)["payload"]
        assert [row["session_id"] for row in payload["sessions"]] == ["failed-new"]
        assert payload["total_sessions"] == 1
        assert bare.stdout == listed.stdout

    @pytest.mark.parametrize(
        ("names", "expected_text"),
        [
            pytest.param(["DB_PIN"], "[REDACTED:DB_PIN]", id="listed"),
            pytest.param([], "1234", id="unlisted"),
        ],
    )
    def test_session_list_masks_name_listed_in_config(
        self,
        cli_runner: CliRunner,
        session_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        names: list[str],
        expected_text: str,
    ) -> None:
        monkeypatch.setenv("DB_PIN", "1234")
        paths = _paths_for(session_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        db.sessions.create(
            session_id="session-pin", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        db.sessions.update_status("session-pin", SessionStatus.FAILED, error_message="1234")
        config = json.loads(paths.config_file.read_text(encoding="utf-8"))
        config["environment"] = {"sensitive_variables": names}
        Filesystem.atomic_write_json(paths.config_file, config)

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "list", "--format", "json"])

        assert result.exit_code == 0
        sessions = json.loads(result.stdout)["payload"]["sessions"]
        assert [row["error_message"] for row in sessions] == [expected_text]
