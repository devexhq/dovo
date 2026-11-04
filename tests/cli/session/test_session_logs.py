"""Single-tier CLI integration tests for dovo session logs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.sessions import LogsShowResult, SessionLogEvent, SessionLogEventType
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result
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

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("session_id", "exit_code", "error_code"),
        [
            pytest.param("sess-logs", 0, None, id="known_session"),
            pytest.param("ghost-session", 1, "SESSION_NOT_FOUND", id="unknown_session"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        output_format: str,
        session_id: str,
        exit_code: int,
        error_code: str | None,
    ) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "logs", session_id, "--format", output_format]
        )

        assert_exit_follows_result(result, output_format, exit_code, error_code)

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
            LogsShowResult(session_id="sess-logs", lines=["err-3", "err-4", "err-5", "err-6", "err-7"])
        ]

    def test_logs_command_unknown_session_exits_1_with_not_found_message(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "ghost-session"])

        assert result.exit_code == 1
        assert "Logs Not Found" in result.stdout
        assert "Session 'ghost-session' not found." in result.stdout
        assert "- Run 'dovo session list'" in result.stdout

    def test_logs_command_session_without_log_directory_exits_1_with_no_logs_message(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        paths = initialized_workspace_paths(cli_workspace)
        DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.create(
            session_id="sess-bare", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "sess-bare"])

        assert result.exit_code == 1
        assert "Logs Not Found" in result.stdout
        assert "No logs found for session 'sess-bare'." in result.stdout

    @pytest.mark.parametrize(
        ("options", "expected_code", "expected_error", "expected_available"),
        [
            pytest.param(
                ["--step", "missing"],
                "SESSION_STEP_NOT_FOUND",
                "Step 'missing' not found in session 'sess-logs'. Available steps: build.",
                {"available_steps": ["build"], "available_attempts": []},
                id="step_not_found",
            ),
            pytest.param(
                ["--step", "build", "--attempt", "9"],
                "SESSION_ATTEMPT_NOT_FOUND",
                "Attempt 9 not found for step 'build' in session 'sess-logs'. Available attempts: 1, 2.",
                {"available_steps": [], "available_attempts": [1, 2]},
                id="attempt_not_found",
            ),
        ],
    )
    def test_logs_command_json_reports_unknown_step_or_attempt_by_error_code(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        options: list[str],
        expected_code: str,
        expected_error: str,
        expected_available: dict[str, list[object]],
    ) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "logs", "sess-logs", *options, "--format", "json"]
        )

        payload = json.loads(result.stdout)["payload"]
        assert result.exit_code == 1
        assert "status" not in payload
        assert payload["error_code"] == expected_code
        assert payload["errors"] == [expected_error]
        assert {key: payload[key] for key in expected_available} == expected_available

    def test_logs_command_unknown_step_prints_step_not_found_panel(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "sess-logs", "--step", "nope"])

        assert result.exit_code == 1
        assert "Step Not Found" in result.stdout
        assert "Available steps: build." in result.stdout

    def test_logs_command_format_json_matches_wire_schema(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "logs", "sess-logs", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "LogsShowResult"
        assert "status" not in event["payload"]
        assert (event["payload"]["error_code"], event["payload"]["errors"]) == (None, [])
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
