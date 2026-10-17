"""Single-tier CLI integration tests for dovo session show."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem import Filesystem
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

    def test_session_show_cli_json_emits_literal_wire_payload(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        db = DovoDb(
            database_file=initialized_workspace_paths(cli_workspace).database_file,
            project_id=initialized_workspace_paths(cli_workspace).project_id,
        )
        db.sessions.create(
            session_id="session-known",
            blueprint_name="task-a",
            blueprint_key="task-a",
            status=SessionStatus.COMPLETED,
            use_worktree=False,
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", "session-known", "--format", "json"]
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
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        worktree_path = display_path(initialized_workspace_paths(cli_workspace).worktree_dir("wt-show"), Path.cwd())
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="wt-show", blueprint_name="task-a", blueprint_key="task-a")
        sessions.update_status("wt-show", SessionStatus.FAILED, worktree_id="wt-show", worktree_kept=True)

        terminal = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "wt-show"])
        wire = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "wt-show", "--format", "json"])

        assert terminal.exit_code == 0
        assert re.search(rf"Worktree:\s+kept \({re.escape(worktree_path)}\)", terminal.stdout)
        payload = json.loads(wire.stdout)["payload"]
        assert payload["worktree_kept"] is True
        assert payload["worktree_path"] == worktree_path

    def test_session_show_cli_removed_worktree_prints_removed_line(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="wt-show", blueprint_name="task-a", blueprint_key="task-a")
        sessions.update_status("wt-show", SessionStatus.COMPLETED, worktree_id="wt-show", worktree_kept=False)

        terminal = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "wt-show"])
        wire = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "wt-show", "--format", "json"])

        assert terminal.exit_code == 0
        assert re.search(r"Worktree:\s+removed", terminal.stdout)
        payload = json.loads(wire.stdout)["payload"]
        assert payload["worktree_kept"] is False
        assert payload["worktree_path"] is None

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
        """Equal the literal list of StepRunSummary dicts in payload['steps'] with every earlier key unchanged."""
        _seed_failed_session(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", "session-failed", "--format", "json"]
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
                "rejected_patch_path": None,
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
                "rejected_patch_path": None,
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

    def test_session_show_cli_session_without_state_warns(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        initialized_workspace_db(cli_workspace).sessions.create(
            session_id="session-bare", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-bare"])

        assert result.exit_code == 0
        assert "No step data recorded for this session." in result.stdout
        assert "Steps" not in result.stdout


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

    def test_show_without_id_json_omits_latest_line(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="session-older", blueprint_name="task-a", blueprint_key="task-a")
        sessions.create(session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "--format", "json"])

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["event_type"] == "HistoryShowResult"
        assert envelope["payload"]["session_id"] == "session-newer"
        assert "(latest)" not in result.stdout

    def test_show_explicit_id_prints_no_latest_line(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        sessions = initialized_workspace_db(cli_workspace).sessions
        sessions.create(session_id="session-older", blueprint_name="task-a", blueprint_key="task-a")
        sessions.create(session_id="session-newer", blueprint_name="task-b", blueprint_key="task-b")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-older"])

        assert result.exit_code == 0
        assert "session-older" in result.stdout
        assert "(latest)" not in result.stdout

    def test_show_without_sessions_exits_one_with_create_session_fix(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show"])

        assert result.exit_code == 1
        assert "Session Not Found" in result.stdout
        assert "No sessions found." in result.stdout
        assert "Run `dovo run <blueprint>` to create a session" in result.stdout


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
        monkeypatch: pytest.MonkeyPatch,
        names: list[str],
        expected_text: str,
    ) -> None:
        monkeypatch.setenv("DB_PIN", "1234")
        paths = initialized_workspace_paths(cli_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        db.sessions.create(
            session_id="session-pin", blueprint_name="task-a", blueprint_key="task-a", status=SessionStatus.COMPLETED
        )
        db.sessions.update_status("session-pin", SessionStatus.FAILED, error_message="1234")
        config = json.loads(paths.config_file.read_text(encoding="utf-8"))
        config["environment"] = {"sensitive_variables": names}
        Filesystem.atomic_write_json(paths.config_file, config)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-pin"])

        assert result.exit_code == 0
        assert expected_text in result.stdout


class SessionShowRejectedPatchCliIntegrationTests:
    def test_json_step_carries_rejected_patch_path_and_terminal_prints_discarded_line(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        paths = initialized_workspace_paths(cli_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        seed_failed_session(
            paths,
            db.sessions,
            session_id="session-rejected",
            steps=[{"id": "format", "run": "true"}, {"id": "code", "run": "false"}],
            failed_step_id="code",
            rejected_patch_path="/s/rejected/code_attempt_1.patch",
        )

        json_result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "show", "session-rejected", "--format", "json"]
        )
        terminal_result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "session-rejected"])

        assert json_result.exit_code == 0
        steps = json.loads(json_result.stdout)["payload"]["steps"]
        assert {step["step_id"]: step["rejected_patch_path"] for step in steps} == {
            "format": None,
            "code": "/s/rejected/code_attempt_1.patch",
        }
        assert terminal_result.exit_code == 0
        assert "Discarded edit saved to /s/rejected/code_attempt_1.patch." in terminal_result.stdout
