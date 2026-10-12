"""Single-tier CLI integration tests for dovo session show."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.utils import display_path
from dovo.core.db import DovoDb, SessionStatus
from tests.harness.sessions import seed_failed_session
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _db_for(workspace: Path) -> DovoDb:
    """Construct a DovoDb bound to workspace's resolved database file and project id."""
    paths = _paths_for(workspace)
    return DovoDb(database_file=paths.database_file, project_id=paths.project_id)


def _seed_failed_session(workspace: Path) -> None:
    """Seed session-failed: format completed, tests failed with exit 1."""
    paths = _paths_for(workspace)
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

    def test_session_show_cli_known_session_exits_zero(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        """dovo session show <session_id>: known session, exit 0, session ID and blueprint name in stdout."""
        db = DovoDb(
            database_file=_paths_for(session_workspace).database_file,
            project_id=_paths_for(session_workspace).project_id,
        )
        db.sessions.create(
            session_id="session-known", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "session-known"])

        assert result.exit_code == 0
        assert "session-known" in result.stdout
        assert "task-a" in result.stdout

    def test_session_show_cli_unknown_session_exits_one(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        """dovo session show <unknown-id>: exit 1, 'not found' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.stdout
        assert "Run `dovo session list`" in result.stdout

    def test_session_show_cli_json_emits_literal_wire_payload(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """dovo session show <session_id> --format json: stdout equals the literal HistoryShowResult envelope."""
        db = DovoDb(
            database_file=_paths_for(session_workspace).database_file,
            project_id=_paths_for(session_workspace).project_id,
        )
        db.sessions.create(
            session_id="session-known",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
            use_worktree=False,
        )

        result = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "show", "session-known", "--format", "json"]
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
            "steps": [],
            "worktree_kept": None,
            "worktree_path": None,
            "errors": [],
            "warnings": ["No step data recorded for this session."],
            "fixes": [],
        }

    def test_session_show_cli_kept_worktree_prints_kept_line_and_json_fields(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo session show <id>: a failed session with worktree_kept True prints "Worktree: kept (<path>)", and --format json carries worktree_kept == true and worktree_path == "<path>"."""
        worktree_path = display_path(_paths_for(session_workspace).worktree_dir("wt-show"), Path.cwd())
        sessions = _db_for(session_workspace).sessions
        sessions.create(session_id="wt-show", blueprint_name="task-a", blueprint_key="task-a")
        sessions.update_status("wt-show", SessionStatus.FAILED, worktree_id="wt-show", worktree_kept=True)

        terminal = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "wt-show"])
        wire = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "wt-show", "--format", "json"])

        assert terminal.exit_code == 0
        assert re.search(rf"Worktree:\s+kept \({re.escape(worktree_path)}\)", terminal.stdout)
        payload = json.loads(wire.stdout)["payload"]
        assert payload["worktree_kept"] is True
        assert payload["worktree_path"] == worktree_path

    def test_session_show_cli_removed_worktree_prints_removed_line(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo session show <id>: a completed worktree-backed session with worktree_kept False prints "Worktree: removed", and --format json carries worktree_kept == false and worktree_path == null."""
        sessions = _db_for(session_workspace).sessions
        sessions.create(session_id="wt-show", blueprint_name="task-a", blueprint_key="task-a")
        sessions.update_status("wt-show", SessionStatus.COMPLETED, worktree_id="wt-show", worktree_kept=False)

        terminal = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "wt-show"])
        wire = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "wt-show", "--format", "json"])

        assert terminal.exit_code == 0
        assert re.search(r"Worktree:\s+removed", terminal.stdout)
        payload = json.loads(wire.stdout)["payload"]
        assert payload["worktree_kept"] is False
        assert payload["worktree_path"] is None

    def test_session_show_cli_failed_session_prints_steps_table(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo session show <id>: seeded FAILED session (format completed, tests failed exit 1 'seeded failure') -> exit 0; stdout has the Steps header, rows for format and tests with states completed/failed, '• tests: seeded failure', and "Use 'dovo logs <id> --step <step_id>' for step output."."""
        _seed_failed_session(session_workspace)

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "session-failed"])

        assert result.exit_code == 0
        assert "Steps" in result.stdout
        assert re.search(r"1[\s│]+format[\s│]+completed[\s│]+1[\s│]+0[\s│]+0\.5s", result.stdout)
        assert re.search(r"2[\s│]+tests[\s│]+failed[\s│]+1[\s│]+1[\s│]+0\.5s", result.stdout)
        assert "• tests: seeded failure" in result.stdout
        assert "Use 'dovo logs session-failed --step <step_id>' for step output." in result.stdout

    def test_session_show_cli_json_includes_steps(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        """[tier-3/integration] dovo session show <id> --format json: seeded FAILED session -> payload['steps'] equals the literal list of StepRunSummary dicts and every previous payload key is unchanged."""
        _seed_failed_session(session_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "show", "session-failed", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)["payload"]
        assert payload["steps"] == [
            {
                "kind": "step",
                "step_id": "format",
                "name": None,
                "state": "completed",
                "attempts": 1,
                "exit_code": 0,
                "duration_seconds": 0.5,
                "error_message": None,
                "loop_id": None,
                "iteration": None,
                "iterations": None,
            },
            {
                "kind": "step",
                "step_id": "tests",
                "name": None,
                "state": "failed",
                "attempts": 1,
                "exit_code": 1,
                "duration_seconds": 0.5,
                "error_message": "seeded failure",
                "loop_id": None,
                "iteration": None,
                "iterations": None,
            },
        ]
        assert set(payload) == {
            "status",
            "session_id",
            "session",
            "log_files",
            "log_snippet",
            "steps",
            "worktree_kept",
            "worktree_path",
            "errors",
            "warnings",
            "fixes",
        }

    def test_session_show_cli_session_without_state_warns(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        """[tier-3/integration] dovo session show <id>: row created without execution state -> exit 0; stdout contains 'No step data recorded for this session.'; no Steps table."""
        _db_for(session_workspace).sessions.create(
            session_id="session-bare", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "session-bare"])

        assert result.exit_code == 0
        assert "No step data recorded for this session." in result.stdout
        assert "Steps" not in result.stdout


class SessionShowLatestCliIntegrationTests:
    """Typer runner integration tests for dovo session show with the session id omitted."""

    def test_show_without_id_uses_latest_and_prints_latest_line(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo session show: two sessions, newer by started_at has status FAILED -> exit 0; stdout begins "Session: <newer id> (latest)" and renders that session's metadata."""
        sessions = _db_for(session_workspace).sessions
        sessions.create(
            session_id="session-older", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        sessions.create(
            session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b", status=SessionStatus.FAILED
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show"])

        assert result.exit_code == 0
        assert result.stdout.lstrip().startswith("Session: session-newer (latest)")
        assert "task-b" in result.stdout
        assert "session-older" not in result.stdout

    def test_show_without_id_json_omits_latest_line(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        """[tier-3/integration] dovo session show --format json: stdout is exactly one HistoryShowResult envelope whose payload.session_id is the latest id; no "(latest)" text."""
        sessions = _db_for(session_workspace).sessions
        sessions.create(session_id="session-older", blueprint_name="task-a", blueprint_key="task-a")
        sessions.create(session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b")

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "--format", "json"])

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["event_type"] == "HistoryShowResult"
        assert envelope["payload"]["session_id"] == "session-newer"
        assert "(latest)" not in result.stdout

    def test_show_explicit_id_prints_no_latest_line(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        """[tier-3/integration] dovo session show <id>: stdout does not contain "(latest)"."""
        sessions = _db_for(session_workspace).sessions
        sessions.create(session_id="session-older", blueprint_name="task-a", blueprint_key="task-a")
        sessions.create(session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b")

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "session-older"])

        assert result.exit_code == 0
        assert "session-older" in result.stdout
        assert "(latest)" not in result.stdout

    def test_show_without_sessions_exits_one_with_create_session_fix(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo session show: empty project -> exit 1; stdout contains "Session Not Found" and "Run `dovo run <blueprint>` to create a session"."""
        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show"])

        assert result.exit_code == 1
        assert "Session Not Found" in result.stdout
        assert "No sessions found." in result.stdout
        assert "Run `dovo run <blueprint>` to create a session" in result.stdout


class SessionShowLogsCliIntegrationTests:
    """Typer runner integration tests for dovo session show --logs."""

    def test_session_show_logs_flag_exit_0_lists_log_paths(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        """dovo session show <session_id> --logs: exit 0 and stdout contains every persisted log file name."""
        DovoDb(
            database_file=_paths_for(session_workspace).database_file,
            project_id=_paths_for(session_workspace).project_id,
        ).sessions.create(
            session_id="session-logs", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        session_log_dir = initialized_workspace_paths(session_workspace).logs_dir / "session-logs"
        session_log_dir.mkdir(parents=True)
        (session_log_dir / "session.log").write_text("", encoding="utf-8")
        (session_log_dir / "01_build_attempt_1.stdout.log").write_text("out\n", encoding="utf-8")

        result = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "show", "session-logs", "--logs", "--format", "json"]
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
        session_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        names: list[str],
        expected_text: str,
    ) -> None:
        """[tier-3/integration] dovo session show <id>: config environment.sensitive_variables ["DB_PIN"], DB_PIN="1234", row error_message "1234" prints "[REDACTED:DB_PIN]", exit 0; with the name removed from config it prints "1234"."""
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

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "show", "session-pin"])

        assert result.exit_code == 0
        assert expected_text in result.stdout
