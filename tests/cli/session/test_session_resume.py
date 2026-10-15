"""Single-tier CLI integration tests for dovo session resume."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.config.models import ConfigTier
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.worktree import Worktree
from tests.harness import FakeAgentRunner
from tests.harness.catalog import write_runnable_step
from tests.harness.sessions import seed_paused_ask_session, seed_paused_session
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _seed_paused_session(
    session_workspace: Path, *, session_id: str, steps: list[dict[str, object]], paused_step_id: str
) -> None:
    """Snapshot a blueprint with steps under session_id and pause it at paused_step_id after a failed attempt."""
    paths = _paths_for(session_workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    seed_paused_session(paths, db.sessions, session_id=session_id, steps=steps, paused_step_id=paused_step_id)


_AGENT_STDOUT = b'{"type":"assistant.message","data":{"content":"ok"}}\n{"type":"result","data":{"exitCode":0}}\n'
_AGENT_STEPS: list[dict[str, object]] = [
    {"id": "gate", "run": "true", "on_failure": "continue"},
    {"id": "plan", "type": "agent", "prompt": "Plan the change"},
]


def _fake_copilot(monkeypatch: pytest.MonkeyPatch) -> FakeAgentRunner:
    """Install a FakeAgentRunner at the Copilot process boundary with a usable GH_TOKEN."""
    monkeypatch.setenv("GH_TOKEN", "test-token")
    runner = FakeAgentRunner().returning(stdout=_AGENT_STDOUT)
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    return runner


def _seed_paused_agent_session(workspace: Path, session_id: str) -> None:
    paths = _paths_for(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    Worktree(paths, db=db.worktrees).create(session_id=session_id)
    seed_paused_session(
        paths,
        db.sessions,
        session_id=session_id,
        steps=_AGENT_STEPS,
        paused_step_id="gate",
        use_worktree=True,
        worktree_id=session_id,
    )


class SessionResumeCliIntegrationTests:
    """Typer runner integration tests for dovo session resume."""

    def test_resume_cli_from_paused_state_completes_remaining_steps_exits_zero(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        _seed_paused_session(
            session_workspace,
            session_id="paused-session-1",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "resume", "paused-session-1"])

        assert result.exit_code == 0
        record = DovoDb(
            database_file=_paths_for(session_workspace).database_file,
            project_id=_paths_for(session_workspace).project_id,
        ).sessions.get("paused-session-1")
        assert record is not None
        assert record.status == SessionStatus.COMPLETED
        assert (session_workspace / "resumed.marker").exists()

    def test_resume_cli_unknown_session_exits_one(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "resume", "does-not-exist"])

        assert result.exit_code == 1
        assert "Resume Failed" in result.stdout

    def test_resume_cli_json_format_emits_run_success_event(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        _seed_paused_session(
            session_workspace,
            session_id="paused-session-2",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
            ],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "resume", "paused-session-2", "--format", "json"]
        )

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        success_events = [e for e in events if e["event_type"] == "RunSuccessEvent"]
        assert len(success_events) == 1
        assert success_events[0]["payload"]["status"] == "completed"

    def test_resume_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        session_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """Exit 1 with a tier-attributed Config Error panel when the User tier config.json is malformed.

        The top-level callback resolves config before the resume handler runs,
        so no paused session is looked up.
        """
        ui_dispatcher.set_output_format("terminal")
        write_tier_config(ConfigTier.USER, "{not valid json")

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "resume", "unknown-session"])

        assert result.exit_code == 1
        assert "Config Error" in result.stdout
        assert "Invalid configuration in user layer" in result.stdout

    def test_resume_cli_completes_after_source_catalog_blueprint_deleted(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        write_runnable_step(
            session_workspace, key="lint-check", definition={"id": "lint-check", "type": "command", "command": "true"}
        )
        _seed_paused_session(
            session_workspace,
            session_id="snap-resume-1",
            steps=[
                {"id": "s1", "uses": "lint-check"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
            paused_step_id="s2",
        )
        (session_workspace / ".dovo" / "catalog" / "blueprints" / "snap-resume-1.yml").unlink()
        (session_workspace / ".dovo" / "catalog" / "steps" / "lint-check.yml").unlink()

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "resume", "snap-resume-1"])

        assert result.exit_code == 0
        record = DovoDb(
            database_file=_paths_for(session_workspace).database_file,
            project_id=_paths_for(session_workspace).project_id,
        ).sessions.get("snap-resume-1")
        assert record is not None
        assert record.status == SessionStatus.COMPLETED
        assert (session_workspace / "resumed.marker").exists()


class SessionResumeEnvFlagsCliIntegrationTests:
    """Typer runner integration tests for the dovo session resume --env-mode and --env-passthrough flags."""

    def test_resume_cli_env_flags_reach_the_resumed_agent_subprocess_env(
        self, cli_runner: CliRunner, session_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv("DOVO_TEST_UNRELATED_SECRET", "host-value")
        monkeypatch.setenv("NAME_HOST", "named")
        _seed_paused_agent_session(session_workspace, "paused-env")

        result = cli_runner.invoke(
            app,
            [
                "-p", str(session_workspace), "session", "resume", "paused-env",
                "--env-mode", "inherit", "--env-passthrough", "NAME_HOST",
            ],
        )  # fmt: skip

        assert result.exit_code == 0
        assert runner.last_call.env["DOVO_TEST_UNRELATED_SECRET"] == "host-value"
        assert runner.last_call.env["NAME_HOST"] == "named"

    def test_resume_cli_flags_do_not_persist_to_a_later_resume(
        self, cli_runner: CliRunner, session_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv("NAME_HOST", "named")
        _seed_paused_agent_session(session_workspace, "paused-first")
        _seed_paused_agent_session(session_workspace, "paused-second")
        config_path = session_workspace / ".dovo" / "config.json"
        before = config_path.read_bytes()

        first = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "resume", "paused-first", "--env-passthrough", "NAME_HOST"]
        )
        second = cli_runner.invoke(app, ["-p", str(session_workspace), "session", "resume", "paused-second"])

        assert (first.exit_code, second.exit_code) == (0, 0)
        assert ["NAME_HOST" in call.env for call in runner.calls] == [True, False]
        assert config_path.read_bytes() == before

    def test_resume_cli_invalid_passthrough_exits_one_and_leaves_session_paused(
        self, cli_runner: CliRunner, session_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        _seed_paused_agent_session(session_workspace, "paused-bad")

        result = cli_runner.invoke(
            app, ["-p", str(session_workspace), "session", "resume", "paused-bad", "--env-passthrough", "=X"]
        )

        paths = _paths_for(session_workspace)
        record = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.get("paused-bad")
        assert result.exit_code == 1
        assert "Invalid --env-passthrough value '=X':" in result.stdout
        assert record is not None
        assert record.status == SessionStatus.PAUSED
        assert runner.calls == []


_ASK_STEPS: list[dict[str, object]] = [
    {
        "id": "approve",
        "type": "ask",
        "interaction": {
            "mode": "select",
            "message": "Approve this plan?",
            "options": [
                {"value": "approve", "label": "Approve", "comment": "disabled"},
                {"value": "revise", "label": "Request changes", "comment": "required"},
                {"value": "deny", "label": "Deny", "fail": True},
            ],
        },
    },
    {"id": "done", "type": "command", "command": "touch done.marker"},
]


def _seed_paused_ask(workspace: Path, session_id: str = "ask-session") -> str:
    """Seed a session paused at the unanswered `approve` ask and return its id."""
    paths = _paths_for(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    seed_paused_ask_session(paths, db.sessions, db.artifacts, session_id=session_id, steps=_ASK_STEPS)
    return session_id


def _session_status(workspace: Path, session_id: str) -> SessionStatus:
    """Return the persisted status of session_id."""
    paths = _paths_for(workspace)
    row = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.get(session_id)
    assert row is not None
    return row.status


class SessionResumeAskCliIntegrationTests:
    """Typer runner integration tests for dovo resume over a session paused at an ask."""

    @pytest.mark.parametrize(
        "command", [pytest.param(["resume"], id="resume"), pytest.param(["session", "resume"], id="session_resume")]
    )
    @pytest.mark.parametrize("style", ["inline", "comment_file", "answers_file"])
    def test_answer_flags_bind_and_continue_the_run(
        self, cli_runner: CliRunner, session_workspace: Path, tmp_path: Path, command: list[str], style: str
    ) -> None:
        """Complete the paused ask via `--answer` with `--comment` or `--comment-file`, or via `--answers-file`.

        Cover both `dovo resume` and `dovo session resume`; each exits 0 and writes the downstream marker.
        """
        session_id = _seed_paused_ask(session_workspace)
        comment_file = tmp_path / "comment.txt"
        comment_file.write_text("Keep config compatible\n", encoding="utf-8")
        answers_file = tmp_path / "answers.json"
        answers_file.write_text(
            json.dumps({"step_id": "approve", "selected": ["revise"], "comment": "Keep config compatible"}),
            encoding="utf-8",
        )
        flags = {
            "inline": ["--answer", "approve=revise", "--comment", "Keep config compatible"],
            "comment_file": ["--answer", "approve=revise", "--comment-file", str(comment_file)],
            "answers_file": ["--answers-file", str(answers_file)],
        }[style]

        result = cli_runner.invoke(app, ["-p", str(session_workspace), *command, session_id, *flags])

        assert result.exit_code == 0, result.stdout
        assert (session_workspace / "done.marker").exists()
        assert _session_status(session_workspace, session_id) == SessionStatus.COMPLETED

    def test_invalid_answer_exits_one_with_valid_choices_and_stays_paused(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        session_id = _seed_paused_ask(session_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(session_workspace), "resume", session_id, "--answer", "approve=maybe"]
        )

        assert result.exit_code == 1
        assert "'maybe' is not a valid answer for prompt 'approve'." in result.stdout
        assert "Fix: use one of: approve, revise, deny" in result.stdout
        assert _session_status(session_workspace, session_id) == SessionStatus.PAUSED
        assert not (session_workspace / "done.marker").exists()

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param("answers_file_with_answer", id="answers_file_with_answer"),
            pytest.param("comment_with_comment_file", id="comment_with_comment_file"),
            pytest.param("comment_alone", id="comment_alone"),
            pytest.param("unreadable_answers_file", id="unreadable_answers_file"),
            pytest.param("invalid_json_answers_file", id="invalid_json_answers_file"),
        ],
    )
    def test_argument_errors_exit_one_before_any_resume(
        self, cli_runner: CliRunner, session_workspace: Path, tmp_path: Path, case: str
    ) -> None:
        session_id = _seed_paused_ask(session_workspace)
        valid_file = tmp_path / "valid.json"
        valid_file.write_text(json.dumps({"step_id": "approve", "selected": ["approve"]}), encoding="utf-8")
        broken_file = tmp_path / "broken.json"
        broken_file.write_text("{not json", encoding="utf-8")
        text_file = tmp_path / "comment.txt"
        text_file.write_text("c", encoding="utf-8")
        flags = {
            "answers_file_with_answer": ["--answers-file", str(valid_file), "--answer", "approve=approve"],
            "comment_with_comment_file": [
                "--answer",
                "approve=revise",
                "--comment",
                "c",
                "--comment-file",
                str(text_file),
            ],
            "comment_alone": ["--comment", "c"],
            "unreadable_answers_file": ["--answers-file", str(tmp_path / "missing.json")],
            "invalid_json_answers_file": ["--answers-file", str(broken_file)],
        }[case]

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "resume", session_id, *flags])

        assert result.exit_code == 1
        assert _session_status(session_workspace, session_id) == SessionStatus.PAUSED
        assert not (session_workspace / "done.marker").exists()

    def test_answers_with_no_pending_prompt_exit_one(self, cli_runner: CliRunner, session_workspace: Path) -> None:
        _seed_paused_session(
            session_workspace,
            session_id="paused-session-1",
            steps=[{"id": "a", "run": "true", "on_failure": "continue"}],
            paused_step_id="a",
        )

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "resume", "paused-session-1", "--answer", "a=b"])

        assert result.exit_code == 1
        assert "No prompt is pending for session 'paused-session-1'." in result.stdout

    def test_no_tty_resume_re_asks_and_pauses_again_exit_zero(
        self, cli_runner: CliRunner, session_workspace: Path
    ) -> None:
        session_id = _seed_paused_ask(session_workspace)

        result = cli_runner.invoke(app, ["-p", str(session_workspace), "resume", session_id, "--no-tty"])

        assert result.exit_code == 0, result.stdout
        assert (
            f"Run paused at prompt 'approve'. Answer with 'dovo resume {session_id} --answer approve=<value>'."
            in result.stdout
        )
        assert _session_status(session_workspace, session_id) == SessionStatus.PAUSED
