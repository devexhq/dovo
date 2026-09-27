"""Single-tier CLI integration tests for wt history show."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from worktree.cli import app
from worktree.core.db import RunStatus, WorktreeDb
from worktree.core.project.services.storage import resolve_project_filesystem_paths


class HistoryShowCliIntegrationTests:
    """Typer runner integration tests for wt history show."""

    def test_history_show_cli_known_session_exits_zero(self, cli_runner: CliRunner, history_workspace: Path) -> None:
        """wt history show <session_id>: known session, exit 0, session ID and blueprint name in stdout."""
        db = WorktreeDb(path=history_workspace)
        db.runs.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "show", "session-known"])

        assert result.exit_code == 0
        assert "session-known" in result.stdout
        assert "task-a" in result.stdout

    def test_history_show_cli_unknown_session_exits_one(self, cli_runner: CliRunner, history_workspace: Path) -> None:
        """wt history show <unknown-id>: exit 1, 'not found' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(history_workspace), "history", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_history_show_cli_json_emits_literal_wire_payload(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """wt history show <session_id> --format json: stdout equals the literal HistoryShowResult envelope."""
        db = WorktreeDb(path=history_workspace)
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
            "checkpoint": None,
            "checkpoint_raw": None,
            "log_files": [],
            "log_snippet": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        }


class HistoryShowLogsCliIntegrationTests:
    """Typer runner integration tests for wt history show --logs."""

    def test_history_show_logs_flag_exit_0_lists_log_paths(
        self, cli_runner: CliRunner, history_workspace: Path
    ) -> None:
        """wt history show <session_id> --logs: exit 0 and stdout contains every persisted log file name."""
        WorktreeDb(path=history_workspace).runs.create(
            session_id="session-logs", blueprint_name="task-a", blueprint_key="task-a", status=RunStatus.COMPLETED
        )
        session_log_dir = resolve_project_filesystem_paths(history_workspace).logs_dir / "session-logs"
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
