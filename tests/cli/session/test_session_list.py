"""Single-tier CLI integration tests for dovo session / dovo session list."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.engine.state_models import NodeState
from tests.harness.diagnostic_log import FROZEN_EVENT_ID
from tests.harness.exit_codes import OUTPUT_FORMATS, assert_exit_follows_result
from tests.harness.lock import lock_timeout_reported, time_out_first_lock
from tests.harness.sessions import INTERRUPTED_ATTEMPT_MESSAGE, fail_reconciliation_list, seed_dead_session, stored_leaf
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


def _list_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave SessionsRepository.list unpatched."""


def _fail_every_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch SessionsRepository.list so every call raises RuntimeError("db unavailable")."""

    def _list(self: SessionsRepository, **kwargs: object) -> NoReturn:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(SessionsRepository, "list", _list)


class SessionListCliIntegrationTests:
    """Typer runner integration tests for dovo session / dovo session list."""

    @pytest.mark.parametrize("output_format", OUTPUT_FORMATS)
    @pytest.mark.parametrize(
        ("patch_listing", "exit_code", "error_code"),
        [
            pytest.param(_list_normally, 0, None, id="listing_succeeds"),
            pytest.param(_fail_every_list, 1, "INTERNAL_ERROR", id="listing_raises"),
        ],
    )
    def test_exit_code_follows_result(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        output_format: str,
        patch_listing: Callable[[pytest.MonkeyPatch], None],
        exit_code: int,
        error_code: str | None,
    ) -> None:
        patch_listing(monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list", "--format", output_format])

        assert_exit_follows_result(result, output_format, exit_code, error_code)

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
        assert event["event_type"] == "session.list"
        assert [row["session_id"] for row in event["payload"]["sessions"]] == ["session-failed", "session-completed"]

    def test_session_list_cli_json_reports_reconciliation_record_in_diagnostics(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exit 0 and emit the sessions with no warning for the failed reconciliation and diagnostics [RECORD_JSON]."""
        db = initialized_workspace_db(cli_workspace)
        db.sessions.create(
            session_id="session-completed",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
        )
        fail_reconciliation_list(monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list", "--format", "json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert [row["session_id"] for row in payload["sessions"]] == ["session-completed"]
        assert payload["warnings"] == []
        assert payload["diagnostics"] == [{"event_id": FROZEN_EVENT_ID, "log_path": str(frozen_diagnostic_clock)}]

    def test_session_list_cli_json_under_lock_timeout_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with the lock timeout reported for `session list --format json`."""
        time_out_first_lock(monkeypatch)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list", "--format", "json"])

        assert result.exit_code == 1
        assert lock_timeout_reported(result.stdout)

    def test_session_list_closes_interrupted_step_of_failed_session(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """A dead session session list marks failed has its running tests step stored failed on its closed attempt."""
        sessions_db = initialized_workspace_db(cli_workspace).sessions
        seed_dead_session(initialized_workspace_paths(cli_workspace), sessions_db, session_id="session-dead")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "list", "--format", "json"])

        assert result.exit_code == 0
        leaf = stored_leaf(sessions_db, "session-dead", "tests")
        assert leaf.state is NodeState.FAILED
        assert [(a.result.exit_code, a.result.error_message) for a in leaf.attempts if a.result is not None] == [
            (1, INTERRUPTED_ATTEMPT_MESSAGE)
        ]

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
