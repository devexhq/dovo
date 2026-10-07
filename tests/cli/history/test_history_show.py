"""Single-tier CLI integration tests for dovo history show."""

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


class HistoryShowCliIntegrationTests:
    """Typer runner integration tests for dovo history show."""

    def test_history_show_cli_known_session_exits_zero(self, cli_runner: CliRunner, history_workspace: Path) -> None:
        """dovo history show <session_id>: known session, exit 0, session ID and blueprint name in stdout."""
        db = DovoDb(
            database_file=_paths_for(history_workspace).database_file,
            project_id=_paths_for(history_workspace).project_id,
        )
        db.sessions.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "show", "session-known"])

        assert result.exit_code == 0
        assert "session-known" in result.stdout
        assert "task-a" in result.stdout

    def test_history_show_cli_unknown_session_exits_one(self, cli_runner: CliRunner, history_workspace: Path) -> None:
        """dovo history show <unknown-id>: exit 1, 'not found' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_history_show_cli_json_emits_literal_wire_payload(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """dovo history show <session_id> --format json: stdout equals the literal HistoryShowResult envelope."""
        db = DovoDb(
            database_file=_paths_for(history_workspace).database_file,
            project_id=_paths_for(history_workspace).project_id,
        )
        db.sessions.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(
            app, ["-p", str(history_workspace), "history", "show", "session-known", "--format", "json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["event_type"] == "HistoryShowResult"
        payload = data["payload"]
        assert payload["session"] is not None
        assert isinstance(payload["session"]["started_at"], str)
        payload["session"]["started_at"] = "<timestamp>"
        assert payload == {
            "status": "ok",
            "session_id": "session-known",
            "session": {
                "session_id": "session-known",
                "blueprint_name": "task-a",
                "status": "completed",
                "branch_name": None,
                "started_at": "<timestamp>",
                "completed_at": None,
                "duration_seconds": None,
                "error_message": None,
            },
            "log_files": [],
            "log_snippet": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        }


class HistoryShowLogsCliIntegrationTests:
    """Typer runner integration tests for dovo history show --logs."""

    def test_history_show_logs_flag_exit_0_lists_log_paths(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """dovo history show <session_id> --logs: exit 0 and stdout contains every persisted log file name."""
        DovoDb(
            database_file=_paths_for(history_workspace).database_file,
            project_id=_paths_for(history_workspace).project_id,
        ).sessions.create(
            session_id="session-logs", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        session_log_dir = (
            resolve_workspace_paths(RepositoryPaths.from_root(history_workspace), resolve_global_paths(None)).logs_dir
            / "session-logs"
        )
        session_log_dir.mkdir(parents=True)
        (session_log_dir / "session.log").write_text("", encoding="utf-8")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = cli_runner.invoke(
            app, ["-p", str(history_workspace), "history", "show", "session-logs", "--logs", "--format", "json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["log_files"] == [
            str(session_log_dir / "01_build_attempt_1.stdout.log"),
            str(session_log_dir / "session.log"),
        ]

    @pytest.mark.parametrize(
        ("names", "expected_text"),
        [
            pytest.param(["DB_PIN"], "[REDACTED:DB_PIN]", id="listed"),
            pytest.param([], "1234", id="unlisted"),
        ],
    )
    def test_history_show_masks_name_listed_in_config(
        self,
        cli_runner: CliRunner,
        history_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        names: list[str],
        expected_text: str,
    ) -> None:
        """[tier-3/integration] dovo history show <id>: config environment.sensitive_variables ["DB_PIN"], DB_PIN="1234", row error_message "1234" prints "[REDACTED:DB_PIN]", exit 0; with the name removed from config it prints "1234"."""
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

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "show", "session-pin"])

        assert result.exit_code == 0
        assert expected_text in result.stdout
