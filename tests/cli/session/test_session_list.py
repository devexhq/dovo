"""Single-tier CLI integration tests for dovo session / dovo session list."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import SessionStatus
from tests.harness.workspace_paths import initialized_workspace_db


class SessionListCliIntegrationTests:
    """Typer runner integration tests for dovo session / dovo session list."""

    def test_session_list_cli_format_json_emits_result_event(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        db = initialized_workspace_db(cli_workspace)
        db.sessions.create(
            session_id="session-completed",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
        )
        db.sessions.create(
            session_id="session-failed", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list", "--format", "json"])

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "HistoryListResult"
        assert [row["session_id"] for row in event["payload"]["sessions"]] == ["session-failed", "session-completed"]

    def test_bare_session_and_ls_alias_equal_session_list(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        db = initialized_workspace_db(cli_workspace)
        db.sessions.create(
            session_id="session-one", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        db.sessions.create(
            session_id="session-two", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.COMPLETED
        )

        listed = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list"])
        bare = cli_runner.invoke(app, ["-p", str(cli_workspace), "session"])
        aliased = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "ls"])

        assert (listed.exit_code, bare.exit_code, aliased.exit_code) == (0, 0, 0)
        assert "session-two" in listed.stdout
        assert bare.stdout == listed.stdout
        assert aliased.stdout == listed.stdout

    def test_bare_session_forwards_limit_status_and_format(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        db = initialized_workspace_db(cli_workspace)
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
            app, ["-p", str(cli_workspace), "session", "--status", "failed", "--limit", "1", "--format", "json"]
        )
        listed = cli_runner.invoke(
            app,
            ["-p", str(cli_workspace), "session", "list", "--status", "failed", "--limit", "1", "--format", "json"],
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
        cli_workspace: Path,
        seed_pin_session: Callable[[list[str]], None],
        names: list[str],
        expected_text: str,
    ) -> None:
        seed_pin_session(names)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list", "--format", "json"])

        assert result.exit_code == 0
        sessions = json.loads(result.stdout)["payload"]["sessions"]
        assert [row["error_message"] for row in sessions] == [expected_text]
