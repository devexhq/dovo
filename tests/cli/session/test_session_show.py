"""Single-tier CLI integration tests for dovo session show."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.utils import display_path
from dovo.core.db import DovoDb, SessionStatus
from tests.harness.sessions import seed_failed_session
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


def _seed_failed_session(workspace: Path) -> None:
    """Seed session-failed: format completed, tests failed with exit 1."""
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    seed_failed_session(
        paths,
        db.sessions,
        session_id="session-failed",
        steps=[{"id": "format", "run": "true"}, {"id": "tests", "run": "false"}],
        failed_step_id="tests",
    )


class SessionShowCliIntegrationTests:
    """Typer runner integration tests for dovo session show."""

    def test_session_show_cli_known_session_exits_zero(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        db = DovoDb(
            database_file=initialized_workspace_paths(cli_workspace).database_file,
            project_id=initialized_workspace_paths(cli_workspace).project_id,
        )
        db.sessions.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-known"])

        assert result.exit_code == 0
        assert "session-known" in result.stdout
        assert "task-a" in result.stdout

    def test_session_show_cli_unknown_session_exits_one(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert "Run `dovo session list`" in result.stdout

    def test_session_show_cli_format_json_emits_result_event(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        initialized_workspace_db(cli_workspace).sessions.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", "session-known", "--format", "json"]
        )

        assert result.exit_code == 0
        event = json.loads(result.stdout)
        assert event["event_type"] == "HistoryShowResult"
        assert event["payload"]["status"] == "ok"
        assert event["payload"]["session_id"] == "session-known"

    @pytest.mark.parametrize(
        ("status", "worktree_kept", "line_pattern"),
        [
            pytest.param(SessionStatus.FAILED, True, r"Worktree:\s+kept \({path}\)", id="kept"),
            pytest.param(SessionStatus.COMPLETED, False, r"Worktree:\s+removed", id="removed"),
        ],
    )
    def test_session_show_cli_worktree_outcome_prints_line_and_json_flag(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        status: SessionStatus,
        worktree_kept: bool,
        line_pattern: str,
    ) -> None:
        worktree_path = display_path(initialized_workspace_paths(cli_workspace).worktree_dir("wt-show"), Path.cwd())
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="wt-show", blueprint_name="task-a", blueprint_key="task-a")
        sessions.update_status("wt-show", status, worktree_id="wt-show", worktree_kept=worktree_kept)

        terminal = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "wt-show"])
        wire = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "wt-show", "--format", "json"])

        assert terminal.exit_code == 0
        assert re.search(line_pattern.format(path=re.escape(worktree_path)), terminal.stdout)
        assert json.loads(wire.stdout)["payload"]["worktree_kept"] is worktree_kept

    def test_session_show_cli_failed_session_prints_steps_table(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _seed_failed_session(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-failed"])

        assert result.exit_code == 0
        assert "Steps" in result.stdout
        assert re.search(r"1[\s│]+format[\s│]+completed[\s│]+1[\s│]+0[\s│]+0\.5s", result.stdout)
        assert re.search(r"2[\s│]+tests[\s│]+failed[\s│]+1[\s│]+1[\s│]+0\.5s", result.stdout)
        assert "• tests: seeded failure" in result.stdout
        assert "Use 'dovo logs session-failed --step <step_id>' for step output." in result.stdout

    def test_session_show_cli_json_includes_steps(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_failed_session(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", "session-failed", "--format", "json"]
        )

        assert result.exit_code == 0
        steps = json.loads(result.stdout)["payload"]["steps"]
        assert [(step["step_id"], step["state"]) for step in steps] == [("format", "completed"), ("tests", "failed")]


class SessionShowLatestCliIntegrationTests:
    """Typer runner integration tests for dovo session show with the session id omitted."""

    def test_show_without_id_uses_latest_and_prints_latest_line(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(
            session_id="session-older", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        sessions.create(
            session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show"])

        assert result.exit_code == 0
        assert result.stdout.lstrip().startswith("Session: session-newer (latest)")
        assert "task-b" in result.stdout
        assert "session-older" not in result.stdout

    @pytest.mark.parametrize(
        ("show_args", "shown", "not_shown"),
        [
            pytest.param(["--format", "json"], "session-newer", "session-older", id="json_latest"),
            pytest.param(["session-older"], "session-older", "session-newer", id="explicit_id"),
        ],
    )
    def test_show_prints_no_latest_line(
        self, cli_runner: CliRunner, cli_workspace: Path, show_args: list[str], shown: str, not_shown: str
    ) -> None:
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="session-older", blueprint_name="task-a", blueprint_key="task-a")
        sessions.create(session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", *show_args])

        assert result.exit_code == 0
        assert shown in result.stdout
        assert not_shown not in result.stdout
        assert "(latest)" not in result.stdout


class SessionShowLogsCliIntegrationTests:
    """Typer runner integration tests for dovo session show --logs."""

    def test_session_show_logs_flag_exit_0_lists_log_paths(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        DovoDb(
            database_file=initialized_workspace_paths(cli_workspace).database_file,
            project_id=initialized_workspace_paths(cli_workspace).project_id,
        ).sessions.create(
            session_id="session-logs", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        session_log_dir = initialized_workspace_paths(cli_workspace).logs_dir / "session-logs"
        session_log_dir.mkdir(parents=True)
        (session_log_dir / "session.log").write_text("", encoding="utf-8")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", "session-logs", "--logs", "--format", "json"]
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
    def test_session_show_masks_name_listed_in_config(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        seed_pin_session: Callable[[list[str]], None],
        names: list[str],
        expected_text: str,
    ) -> None:
        seed_pin_session(names)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-pin"])

        assert result.exit_code == 0
        assert expected_text in result.stdout
