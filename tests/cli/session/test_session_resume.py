"""Single-tier CLI integration tests for dovo session resume."""

from __future__ import annotations

import json
import shlex
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.db import DovoDb, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree
from dovo.engine import BlueprintResumeService
from dovo.engine.models import BlueprintRunResult
from dovo.engine.writer import snapshot_blueprint_path
from tests.harness import FakeAgentRunner
from tests.harness.catalog import write_runnable_step
from tests.harness.diagnostic_log import details_line, diagnostic_record, unwrapped
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.run_result import RUN_RESULT, json_events, run_result_payload
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
        assert "Run Failed" in result.stdout
        assert "Resume Failed" not in result.stdout

    def test_resume_cli_json_unknown_session_ends_with_a_run_result_without_session(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        """For an unknown session with --format json, exit 1 and end with SESSION_NOT_FOUND and every session field null."""
        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "does-not-exist", "--format", "json"]
        )

        assert result.exit_code == 1
        assert run_result_payload(result.stdout) == {
            "ok": False,
            "error_code": "SESSION_NOT_FOUND",
            "errors": ["Session 'does-not-exist' not found."],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "session_id": None,
            "blueprint_name": None,
            "session_status": None,
            "paused_prompt_id": None,
        }

    def test_resume_cli_json_format_ends_with_the_completed_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
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
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["errors"]) == (True, None, [])
        assert (payload["session_id"], payload["session_status"], payload["paused_prompt_id"]) == (
            "paused-session-2",
            "completed",
            None,
        )

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


_RESUMABLE_STEPS: list[dict[str, object]] = [
    {"id": "s1", "run": "true"},
    {"id": "s2", "run": "true", "on_failure": "continue"},
    {"id": "s3", "run": "true"},
]


def _seed_invalid_snapshot(cli_workspace: Path, session_id: str, steps: list[dict[str, object]]) -> None:
    """Pause a valid run under session_id, then replace its blueprint snapshot with one holding steps."""
    _seed_paused_session(
        cli_workspace,
        session_id=session_id,
        steps=_RESUMABLE_STEPS,
        paused_step_id="s2",
    )
    snapshot = snapshot_blueprint_path(initialized_workspace_paths(cli_workspace).session_dir(session_id), session_id)
    snapshot.write_text(yaml.safe_dump({"name": session_id, "steps": steps}, sort_keys=False), encoding="utf-8")


_DUPLICATE_ID_STEPS: list[dict[str, object]] = [{"id": "a", "run": "echo one"}, {"id": "a", "run": "echo two"}]


class SessionResumeValidationCliIntegrationTests:
    """Typer runner tests for GH-1096: dovo resume validates the session's blueprint snapshot before any step."""

    def test_invalid_snapshot_prints_run_failed_exits_one_and_stays_paused(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _seed_invalid_snapshot(
            cli_workspace, "bad-snap", [*_DUPLICATE_ID_STEPS, {"id": "b", "type": "agent", "prompt": ""}]
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "bad-snap"])

        assert result.exit_code == 1
        assert "Run Failed" in result.stdout
        assert "steps.2: Value error, Agent steps must specify a non-empty 'prompt' string." in result.stdout
        assert "errors.pydantic.dev" not in result.stdout
        paths = initialized_workspace_paths(cli_workspace)
        record = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.get("bad-snap")
        assert record is not None
        assert record.status == SessionStatus.PAUSED

    def test_invalid_snapshot_json_ends_with_the_run_result(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_invalid_snapshot(cli_workspace, "bad-snap", _DUPLICATE_ID_STEPS)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "bad-snap", "--format", "json"])

        assert result.exit_code == 1
        assert [event["event_type"] for event in json_events(result.stdout)] == ["message", RUN_RESULT]
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["errors"]) == (
            False,
            "CATALOG_SCHEMA_INVALID",
            ["Step id 'a' is used by more than one step."],
        )
        assert payload["remediations"] == [
            {"message": "Resolve: Step id 'a' is used by more than one step.", "command": None}
        ]
        assert (payload["session_id"], payload["session_status"]) == ("bad-snap", "paused")
        assert _session_status(cli_workspace, "bad-snap") == SessionStatus.PAUSED

    def test_snapshot_warning_is_carried_once_on_the_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _seed_invalid_snapshot(
            cli_workspace,
            "warn-snap",
            [*_RESUMABLE_STEPS[:2], {"id": "s3", "run": "true", "env": {"GHOST": "${{ inputs.ghost }}"}}],
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "warn-snap", "--format", "json"]
        )

        assert result.exit_code == 0
        assert run_result_payload(result.stdout)["warnings"] == [
            "Placeholder 'inputs.ghost' is not declared in 'inputs:' (CATALOG_UNDECLARED_INPUT_PLACEHOLDER)."
        ]

    def test_snapshot_warning_prints_once_before_the_outcome(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_invalid_snapshot(
            cli_workspace,
            "warn-snap",
            [*_RESUMABLE_STEPS[:2], {"id": "s3", "run": "true", "env": {"GHOST": "${{ inputs.ghost }}"}}],
        )

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "session", "resume", "warn-snap"])

        assert result.exit_code == 0
        output = unwrapped(result.stdout)
        warning = unwrapped("Warning: Placeholder 'inputs.ghost' is not declared in 'inputs:'")
        assert output.count(warning) == 1
        assert output.index(warning) < output.index(unwrapped("Blueprint Run Completed:"))


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

    def test_resume_cli_invalid_passthrough_exits_two_and_leaves_session_paused(
        self, cli_runner: CliRunner, cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_copilot(monkeypatch)
        _seed_paused_agent_session(cli_workspace, "paused-bad")

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "session", "resume", "paused-bad", "--env-passthrough", "=X"]
        )

        paths = initialized_workspace_paths(cli_workspace)
        record = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.get("paused-bad")
        assert result.exit_code == 2
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

    @pytest.mark.parametrize("output_format", ["terminal", "json"])
    @pytest.mark.parametrize(
        ("case", "exit_code", "error_code"),
        [
            pytest.param("answers_file_with_answer", 2, "INTERACTION_ARGUMENTS_INVALID", id="answers_file_with_answer"),
            pytest.param(
                "comment_with_comment_file", 2, "INTERACTION_ARGUMENTS_INVALID", id="comment_with_comment_file"
            ),
            pytest.param("comment_alone", 2, "INTERACTION_ARGUMENTS_INVALID", id="comment_alone"),
            pytest.param(
                "invalid_json_answers_file", 2, "INTERACTION_ARGUMENTS_INVALID", id="invalid_json_answers_file"
            ),
            pytest.param(
                "unreadable_answers_file", 1, "INTERACTION_ARGUMENT_FILE_UNREADABLE", id="unreadable_answers_file"
            ),
            pytest.param(
                "unreadable_comment_file", 1, "INTERACTION_ARGUMENT_FILE_UNREADABLE", id="unreadable_comment_file"
            ),
            pytest.param(
                "non_utf8_comment_file", 1, "INTERACTION_ARGUMENT_FILE_UNREADABLE", id="non_utf8_comment_file"
            ),
            pytest.param("answer_rejected_by_prompt", 1, "INTERACTION_ANSWER_INVALID", id="answer_rejected_by_prompt"),
        ],
    )
    def test_invalid_answer_arguments_exit_by_their_code(
        self,
        cli_runner: CliRunner,
        cli_workspace: Path,
        tmp_path: Path,
        case: str,
        exit_code: int,
        error_code: str,
        output_format: str,
    ) -> None:
        session_id = _seed_paused_ask(cli_workspace)
        valid_file = tmp_path / "valid.json"
        valid_file.write_text(json.dumps({"step_id": "approve", "selected": ["approve"]}), encoding="utf-8")
        broken_file = tmp_path / "broken.json"
        broken_file.write_text("{not json", encoding="utf-8")
        text_file = tmp_path / "comment.txt"
        text_file.write_text("c", encoding="utf-8")
        binary_file = tmp_path / "binary.bin"
        binary_file.write_bytes(b"\xff\xfe")
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
            "unreadable_comment_file": ["--answer", "approve=revise", "--comment-file", str(tmp_path / "missing.txt")],
            "invalid_json_answers_file": ["--answers-file", str(broken_file)],
            "non_utf8_comment_file": ["--answer", "approve=revise", "--comment-file", str(binary_file)],
            "answer_rejected_by_prompt": ["--answer", "approve=not-an-option"],
        }[case]

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", session_id, *flags, "--format", output_format]
        )

        assert result.exit_code == exit_code
        if output_format == "json":
            assert run_result_payload(result.stdout)["error_code"] == error_code
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
        assert run_result_payload(result.stdout)["warnings"] == [_PAUSED_SENTENCE, _RERUN_WARNING]

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


class SessionResumeResultCliTests:
    """Typer runner tests for #1311: every dovo resume ends with exactly one run result, its last JSON line."""

    def test_json_headless_re_pause_at_ask_is_ok_with_the_hint_just_before_the_run_result(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        session_id = _seed_paused_ask(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", session_id, "--no-tty", "--format", "json"]
        )

        assert result.exit_code == 0
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["session_status"], payload["paused_prompt_id"]) == (
            True,
            None,
            "paused",
            "approve",
        )
        assert json_events(result.stdout)[-2] == {
            "version": 1,
            "event_type": "message",
            "payload": {
                "message": (
                    f"Run paused at prompt 'approve'. Answer with 'dovo resume {session_id} --answer approve=<value>'."
                ),
                "style": None,
            },
        }

    def test_json_failed_step_reports_run_step_failed(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        _seed_paused_session(
            cli_workspace,
            session_id="fail-after-resume",
            steps=[{"id": "s1", "run": "true"}, {"id": "s2", "run": "exit 3"}],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", "fail-after-resume", "--no-tty", "--format", "json"]
        )

        assert result.exit_code == 1
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["session_status"]) == (False, "RUN_STEP_FAILED", "failed")
        assert (payload["session_id"], payload["blueprint_name"]) == ("fail-after-resume", "fail-after-resume")
        assert payload["errors"][0].startswith("Step 's2' failed")

    def test_json_no_paused_session_is_rejected_before_any_step(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", "--format", "json"])

        assert result.exit_code == 1
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"], payload["errors"]) == (
            False,
            "SESSION_NOT_FOUND",
            ["No paused session found to resume."],
        )
        assert (payload["session_id"], payload["blueprint_name"], payload["session_status"]) == (None, None, None)

    def test_json_argument_error_ends_with_the_run_result(self, cli_runner: CliRunner, cli_workspace: Path) -> None:
        session_id = _seed_paused_ask(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", session_id, "--comment", "c", "--format", "json"]
        )

        assert result.exit_code == 2
        assert [event["event_type"] for event in json_events(result.stdout)] == [RUN_RESULT]
        payload = run_result_payload(result.stdout)
        assert (payload["ok"], payload["error_code"]) == (False, "INTERACTION_ARGUMENTS_INVALID")
        assert len(payload["errors"]) == 1
        assert (payload["session_id"], payload["blueprint_name"], payload["session_status"]) == (None, None, None)
        assert _session_status(cli_workspace, session_id) == SessionStatus.PAUSED

    def test_json_malformed_answer_reports_the_fix_as_a_remediation(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        session_id = _seed_paused_ask(cli_workspace)

        result = cli_runner.invoke(
            app, ["-p", str(cli_workspace), "resume", session_id, "--answer", "x", "--format", "json"]
        )

        assert result.exit_code == 2
        payload = run_result_payload(result.stdout)
        assert (payload["error_code"], payload["errors"], payload["remediations"]) == (
            "INTERACTION_ARGUMENTS_INVALID",
            ["'x' is not a valid '--answer' value."],
            [{"message": "Pass '--answer <step-id>=<value>'", "command": None}],
        )

    @pytest.mark.parametrize(
        ("flags", "exit_code"),
        [
            pytest.param(["does-not-exist"], 1, id="failed_resume"),
            pytest.param(["--comment", "c"], 2, id="argument_error"),
        ],
    )
    def test_terminal_titles_match_run(
        self, cli_runner: CliRunner, cli_workspace: Path, flags: list[str], exit_code: int
    ) -> None:
        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "resume", *flags])

        assert result.exit_code == exit_code
        assert "Run Failed" in result.stdout
        assert "Resume Failed" not in result.stdout
