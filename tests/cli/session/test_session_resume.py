"""Single-tier CLI integration tests for dovo session resume."""

from __future__ import annotations

import json
import shlex
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree
from dovo.engine import BlueprintResumeService
from dovo.engine.models import BlueprintRunResult
from tests.harness import FakeAgentRunner
from tests.harness.catalog import write_runnable_step
from tests.harness.diagnostic_log import details_line, diagnostic_record, unwrapped
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.sessions import seed_crashed_session, seed_paused_ask_session, seed_paused_session
from tests.harness.workspace_paths import initialized_workspace_paths


def _seed_paused_session(
    cli_workspace: Path, *, session_id: str, steps: list[dict[str, object]], paused_step_id: str
) -> None:
    """Snapshot a blueprint with steps under session_id and pause it at paused_step_id after a failed attempt."""
    paths = initialized_workspace_paths(cli_workspace)
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
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    Worktree(paths, db=db.worktrees, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id).create(
        session_id=session_id
    )
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
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _seed_paused_session(
            cli_workspace,
            session_id="paused-session-1",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "paused-session-1"])

        assert result.exit_code == 0
        record = DovoDb(
            database_file=initialized_workspace_paths(cli_workspace).database_file,
            project_id=initialized_workspace_paths(cli_workspace).project_id,
        ).sessions.get("paused-session-1")
        assert record is not None
        assert record.status == SessionStatus.COMPLETED
        assert (cli_workspace / "resumed.marker").exists()

    def test_resume_cli_unknown_session_exits_one(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "does-not-exist"])

        assert result.exit_code == 1
        assert "Resume Failed" in result.stdout

    def test_resume_cli_json_failure_emits_error_panel_and_exits_one(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """For an unknown session with --format json, exit 1 and emit one ErrorPanelEvent with the message and no error_code key."""
        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "does-not-exist", "--format", "json"]
        )

        assert result.exit_code == 1
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        panels = [event["payload"] for event in events if event["event_type"] == "ErrorPanelEvent"]
        assert len(panels) == 1
        assert panels[0]["message"] == "Session 'does-not-exist' not found."
        assert "error_code" not in panels[0]

    def test_resume_cli_json_format_emits_run_success_event(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_paused_session(
            cli_workspace,
            session_id="paused-session-2",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
            ],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "paused-session-2", "--format", "json"]
        )

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        success_events = [e for e in events if e["event_type"] == "RunSuccessEvent"]
        assert len(success_events) == 1
        assert success_events[0]["payload"]["status"] == "completed"

    def test_resume_cli_completes_after_source_catalog_blueprint_deleted(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        write_runnable_step(
            cli_workspace, key="lint-check", definition={"id": "lint-check", "type": "command", "command": "true"}
        )
        _seed_paused_session(
            cli_workspace,
            session_id="snap-resume-1",
            steps=[
                {"id": "s1", "uses": "lint-check"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
            paused_step_id="s2",
        )
        (cli_workspace / ".dovo" / "catalog" / "blueprints" / "snap-resume-1.yml").unlink()
        (cli_workspace / ".dovo" / "catalog" / "steps" / "lint-check.yml").unlink()

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "snap-resume-1"])

        assert result.exit_code == 0
        record = DovoDb(
            database_file=initialized_workspace_paths(cli_workspace).database_file,
            project_id=initialized_workspace_paths(cli_workspace).project_id,
        ).sessions.get("snap-resume-1")
        assert record is not None
        assert record.status == SessionStatus.COMPLETED
        assert (cli_workspace / "resumed.marker").exists()


class SessionResumeEnvFlagsCliIntegrationTests:
    """Typer runner integration tests for the dovo session resume --env-mode and --env-passthrough flags."""

    def test_resume_cli_env_flags_reach_the_resumed_agent_subprocess_env(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv("DOVO_TEST_UNRELATED_SECRET", "host-value")
        monkeypatch.setenv("NAME_HOST", "named")
        _seed_paused_agent_session(cli_workspace, "paused-env")

        result = cli_runner.invoke(
            app,
            [
                "-p", str(cli_workspace), "session", "resume", "paused-env",
                "--env-mode", "inherit", "--env-passthrough", "NAME_HOST",
            ],
        )  # fmt: skip

        assert result.exit_code == 0
        assert runner.last_call.env["DOVO_TEST_UNRELATED_SECRET"] == "host-value"
        assert runner.last_call.env["NAME_HOST"] == "named"

    def test_resume_cli_flags_do_not_persist_to_a_later_resume(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv("NAME_HOST", "named")
        _seed_paused_agent_session(cli_workspace, "paused-first")
        _seed_paused_agent_session(cli_workspace, "paused-second")
        config_path = cli_workspace / ".dovo" / "config.json"
        before = config_path.read_bytes()

        first = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "paused-first", "--env-passthrough", "NAME_HOST"]
        )
        second = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "paused-second"])

        assert (first.exit_code, second.exit_code) == (0, 0)
        assert ["NAME_HOST" in call.env for call in runner.calls] == [True, False]
        assert config_path.read_bytes() == before

    def test_resume_cli_invalid_passthrough_exits_one_and_leaves_session_paused(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        _seed_paused_agent_session(cli_workspace, "paused-bad")

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "paused-bad", "--env-passthrough", "=X"]
        )

        paths = initialized_workspace_paths(cli_workspace)
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
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    seed_paused_ask_session(paths, db.sessions, db.artifacts, session_id=session_id, steps=_ASK_STEPS)
    return session_id


def _session_status(workspace: Path, session_id: str) -> SessionStatus:
    """Return the persisted status of session_id."""
    paths = initialized_workspace_paths(workspace)
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
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path, command: list[str], style: str
    ) -> None:
        """Complete the paused ask via `--answer` with `--comment` or `--comment-file`, or via `--answers-file`.

        Cover both `dovo resume` and `dovo session resume`; each exits 0 and writes the downstream marker.
        """
        session_id = _seed_paused_ask(cli_workspace)
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

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *command, session_id, *flags])

        assert result.exit_code == 0, result.stdout
        assert (cli_workspace / "done.marker").exists()
        assert _session_status(cli_workspace, session_id) == SessionStatus.COMPLETED

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
        self, cli_runner: CliRunner, cli_workspace: Path, tmp_path: Path, case: str
    ) -> None:
        session_id = _seed_paused_ask(cli_workspace)
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

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", session_id, *flags])

        assert result.exit_code == 1
        assert _session_status(cli_workspace, session_id) == SessionStatus.PAUSED
        assert not (cli_workspace / "done.marker").exists()

    def test_no_tty_resume_re_asks_and_pauses_again_exit_zero(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session_id = _seed_paused_ask(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", session_id, "--no-tty"])

        assert result.exit_code == 0, result.stdout
        assert (
            f"Run paused at prompt 'approve'. Answer with 'dovo resume {session_id} --answer approve=<value>'."
            in result.stdout
        )
        assert _session_status(cli_workspace, session_id) == SessionStatus.PAUSED


class SessionResumeReconcileCliIntegrationTests:
    """Typer runner regression tests for a resumed session reconciled by a concurrent `dovo status`."""

    def test_resumed_session_survives_status_run_by_its_own_step(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """Resume a backdated session paused at an ask whose next step runs `dovo status`.

        The resume exits 0, the row ends COMPLETED and status.out holds no reconciliation warning.
        """
        dovo = shlex.quote(str(Path(sys.executable).with_name("dovo")))
        paths = initialized_workspace_paths(cli_workspace)
        db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
        seed_paused_ask_session(
            paths,
            db.sessions,
            db.artifacts,
            session_id="resumed",
            steps=[_ASK_STEPS[0], {"id": "status", "type": "command", "command": f"{dovo} status > status.out 2>&1"}],
        )
        connection = sqlite3.connect(paths.database_file)
        try:
            connection.execute(
                "UPDATE sessions SET started_at = '2000-01-01 00:00:00' WHERE session_id = ?", ("resumed",)
            )
            connection.commit()
        finally:
            connection.close()

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", "resumed", "--answer", "approve=approve"])

        assert result.exit_code == 0, result.stdout
        assert _session_status(cli_workspace, "resumed") == SessionStatus.COMPLETED
        assert "interrupted session" not in (cli_workspace / "status.out").read_text(encoding="utf-8")


_PAUSED_SENTENCE = "Paused 1 interrupted session; continue it with 'dovo resume <session_id>'."
_RERUN_WARNING = (
    "Step 'mark' was interrupted before it recorded a result and was run again as attempt 1; "
    "check for side effects its interrupted attempt already made."
)


def _seed_crashed(workspace: Path) -> None:
    """Seed `crashed`, a RUNNING run left by a dead process whose step `mark` writes crashed.marker."""
    paths = initialized_workspace_paths(workspace)
    db = DovoDb(database_file=paths.database_file, project_id=paths.project_id)
    seed_crashed_session(
        paths,
        db.sessions,
        session_id="crashed",
        steps=[{"id": "mark", "type": "command", "command": "touch crashed.marker"}],
        interrupted_step_id="mark",
    )


class SessionResumeCrashedRunCliIntegrationTests:
    """Typer runner tests for resuming a run whose process died, with no reconciling command run first."""

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param(["resume", "crashed"], id="resume"),
            pytest.param(["session", "resume", "crashed"], id="session-resume"),
            pytest.param(["resume"], id="latest-paused"),
        ],
    )
    def test_crashed_run_resumes_and_reruns_the_step(
        self, cli_runner: CliRunner, cli_workspace: Path, args: list[str]
    ) -> None:
        """Exit 0, rerun the interrupted step and print the paused sentence and the rerun warning."""
        _seed_paused_session(
            cli_workspace, session_id="older", steps=[{"id": "s1", "run": "true"}], paused_step_id="s1"
        )
        _seed_crashed(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), *args])

        assert result.exit_code == 0, result.stdout
        assert (cli_workspace / "crashed.marker").exists()
        stdout = unwrapped(result.stdout)
        assert unwrapped(_PAUSED_SENTENCE) in stdout
        assert unwrapped(_RERUN_WARNING) in stdout
        if args == ["resume"]:
            assert "crashed" in stdout
            assert "older" not in stdout

    def test_crashed_run_json_carries_both_warnings(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        """With --format json, exit 0 and emit the paused sentence then the rerun warning."""
        _seed_crashed(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", "crashed", "--format", "json"])

        assert result.exit_code == 0, result.stdout
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        warnings = [event["payload"]["message"] for event in events if event["event_type"] == "WarningEvent"]
        assert warnings == [_PAUSED_SENTENCE, _RERUN_WARNING]

    def test_resumed_crashed_run_shows_no_error_message(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        """After the resume, `dovo session show crashed --format json` reports completed with a null error message."""
        _seed_crashed(cli_workspace)
        assert cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", "crashed"]).exit_code == 0

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "show", "crashed", "--format", "json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout.splitlines()[-1])["payload"]
        assert payload["session"]["status"] == "completed"
        assert payload["session"]["error_message"] is None

    @pytest.mark.parametrize(
        ("fault", "message"),
        [
            pytest.param("corrupt-state", "Execution state for session 'crashed' is corrupt.", id="corrupt-state"),
            pytest.param("missing-snapshot", "Definition snapshot file missing:", id="missing-snapshot"),
        ],
    )
    def test_unresumable_crashed_run_exits_one_and_stays_paused(
        self, cli_runner: CliRunner, cli_workspace: Path, fault: str, message: str
    ) -> None:
        """Exit 1 with the error resume reports for the fault, and leave the reconciled row paused."""
        _seed_crashed(cli_workspace)
        paths = initialized_workspace_paths(cli_workspace)
        if fault == "corrupt-state":
            connection = sqlite3.connect(paths.database_file)
            try:
                connection.execute(
                    "UPDATE sessions SET execution_state_json = '{not json' WHERE session_id = ?", ("crashed",)
                )
                connection.commit()
            finally:
                connection.close()
        else:
            shutil.rmtree(paths.session_dir("crashed") / "definitions")

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", "crashed"])

        assert result.exit_code == 1
        assert unwrapped(message) in unwrapped(result.stdout)
        assert _session_status(cli_workspace, "crashed") == SessionStatus.PAUSED


class SessionResumeCliDiagnosticsTests:
    def test_resume_cli_prints_details_line_after_outcome(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Print one 'Details logged: …' line after the resume outcome when BlueprintResumeService.execute returns a result carrying [diagnostic_record(...)]."""
        _seed_paused_session(
            cli_workspace,
            session_id="paused-diagnostics",
            steps=[{"id": "s1", "run": "true"}, {"id": "s2", "run": "true", "on_failure": "continue"}],
            paused_step_id="s2",
        )
        real_execute = BlueprintResumeService.execute

        def _execute_with_record(self: BlueprintResumeService) -> BlueprintRunResult:
            return real_execute(self).model_copy(update={"diagnostics": [diagnostic_record(frozen_diagnostic_clock)]})

        monkeypatch.setattr(BlueprintResumeService, "execute", _execute_with_record)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "paused-diagnostics"])

        assert result.exit_code == 0
        output = unwrapped(result.stdout)
        line = unwrapped(details_line(frozen_diagnostic_clock))
        assert output.count(unwrapped("Details logged")) == 1
        assert output.endswith(line)
