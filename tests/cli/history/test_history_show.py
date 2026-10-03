"""Single-tier CLI integration tests for dovo history show."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import DovoDb, RunStatus
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
        db.runs.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
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
        db.runs.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
        )

        result = cli_runner.invoke(
            app, ["-p", str(history_workspace), "history", "show", "session-known", "--format", "json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["event_type"] == "HistoryShowResult"
        payload = data["payload"]
        assert payload["run"] is not None
        assert isinstance(payload["run"]["started_at"], str)
        payload["run"]["started_at"] = "<timestamp>"
        assert payload == {
            "status": "ok",
            "session_id": "session-known",
            "run": {
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
        ).runs.create(
            session_id="session-logs", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
        )
        session_log_dir = (
            resolve_workspace_paths(RepositoryPaths.from_root(history_workspace), resolve_global_paths(None)).logs_dir
            / "session-logs"
        )
        session_log_dir.mkdir(parents=True)
        (session_log_dir / "run.log").write_text("", encoding="utf-8")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = cli_runner.invoke(
            app, ["-p", str(history_workspace), "history", "show", "session-logs", "--logs", "--format", "json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["payload"]["log_files"] == [
            str(session_log_dir / "01_build_attempt_1.stdout.log"),
            str(session_log_dir / "run.log"),
        ]
