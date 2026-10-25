"""Single-tier CLI integration tests for dovo session logs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.sessions import LogsShowResult, LogsShowStatus, SessionLogEvent, SessionLogEventType
from tests.harness.workspace_paths import initialized_workspace_paths

_EVENTS = [
    SessionLogEvent(ts="2026-09-26T10:00:00+00:00", event=SessionLogEventType.SESSION_STARTED, session_id="sess-logs"),
    SessionLogEvent(ts="2026-09-26T10:00:01+00:00", event=SessionLogEventType.SESSION_COMPLETED, status="completed"),
]


def _seed_session(workspace: Path) -> Path:
    """Persist a session record plus a session.log and build step captures for session 'sess-logs'."""
    paths = initialized_workspace_paths(workspace)
    DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.create(
        session_id="sess-logs", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
    )
    session_log_dir = paths.logs_dir / "sess-logs"
    session_log_dir.mkdir(parents=True)
    (session_log_dir / "session.log").write_text("".join(e.model_dump_json() + "\n" for e in _EVENTS), encoding="utf-8")
    (session_log_dir / "01_build_attempt_1.stderr.log").write_text("first-attempt\n", encoding="utf-8")
    (session_log_dir / "01_build_attempt_2.stdout.log").write_text("stdout-line\n", encoding="utf-8")
    (session_log_dir / "01_build_attempt_2.stderr.log").write_text(
        "".join(f"err-{i}\n" for i in range(1, 8)), encoding="utf-8"
    )
    return session_log_dir


class SessionLogsCliIntegrationTests:
    """Typer runner integration tests for dovo session logs."""

    def test_logs_command_exit_0_prints_run_log_events_for_valid_session(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "sess-logs"])

        assert result.exit_code == 0
        for event in _EVENTS:
            assert event.ts in result.stdout
            assert event.event.value in result.stdout

    def test_logs_command_step_attempt_stream_tail_options_bind_to_service_call(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(cli_workspace),
                "session",
                "logs",
                "sess-logs",
                "--step",
                "build",
                "--attempt",
                "2",
                "--stream",
                "stderr",
                "--tail",
                "5",
            ],
        )

        assert result.exit_code == 0
        assert dispatch_spy == [
            LogsShowResult(
                status=LogsShowStatus.OK, session_id="sess-logs", lines=["err-3", "err-4", "err-5", "err-6", "err-7"]
            )
        ]

    def test_logs_command_unknown_session_exits_1_with_not_found_message(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "ghost-session"])

        assert result.exit_code == 1
        assert "Logs Not Found" in result.stdout
        assert "No logs found for session 'ghost-session'" in result.stdout
        assert "- Run 'dovo session list'" in result.stdout

    def test_logs_command_format_json_matches_wire_schema(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "sess-logs", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "LogsShowResult"
        assert [row["event"] for row in event["payload"]["events"]] == ["session_started", "session_completed"]

    @pytest.mark.parametrize(
        ("names", "expected_line"),
        [
            pytest.param(["DB_PIN"], "[REDACTED:DB_PIN]", id="listed"),
            pytest.param([], "1234", id="unlisted"),
        ],
    )
    def test_logs_command_masks_name_listed_in_config(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        seed_pin_session: Callable[[list[str]], None],
        names: list[str],
        expected_line: str,
    ) -> None:
        seed_pin_session(names)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "session-pin", "--step", "pin"])

        assert result.exit_code == 0
        assert f"pin {expected_line}" in result.stdout


class SessionLogsLatestCliIntegrationTests:
    """Typer runner integration tests for dovo session logs with the session id omitted."""

    def test_logs_without_id_uses_latest_and_prints_latest_line(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        paths = initialized_workspace_paths(cli_workspace)
        DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.create(
            session_id="sess-older", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )
        _seed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "--step", "build"])

        assert result.exit_code == 0
        assert result.stdout.lstrip().startswith("Session: sess-logs (latest)")
        assert "stdout-line" in result.stdout

    def test_logs_without_id_json_omits_latest_line(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "--format", "json"])

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["event_type"] == "LogsShowResult"
        assert envelope["payload"]["session_id"] == "sess-logs"
        assert "(latest)" not in result.stdout

    def test_logs_without_sessions_exits_one_with_create_session_fix(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs"])

        assert result.exit_code == 1
        assert "Session Not Found" in result.stdout
        assert "- Run 'dovo run <blueprint>' to create a session" in result.stdout
