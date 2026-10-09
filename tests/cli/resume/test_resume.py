"""Single-tier CLI integration tests for dovo resume."""

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
from tests.harness.sessions import seed_paused_session
from tests.harness.workspace_paths import initialized_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


def _seed_paused_session(
    resume_workspace: Path, *, session_id: str, steps: list[dict[str, object]], paused_step_id: str
) -> None:
    """Snapshot a blueprint with steps under session_id and pause it at paused_step_id after a failed attempt."""
    paths = _paths_for(resume_workspace)
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
    monkeypatch.setattr("dovo.core.agents.copilot.run_isolated_process", runner)

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


class ResumeCliIntegrationTests:
    """Typer runner integration tests for dovo resume."""

    def test_resume_cli_from_paused_state_completes_remaining_steps_exits_zero(
        self, cli_runner: CliRunner, resume_workspace: Path
    ) -> None:
        """dovo resume <session_id>: paused run state with use_worktree=False resumes and completes, exit 0, session record status becomes COMPLETED."""
        _seed_paused_session(
            resume_workspace,
            session_id="paused-session-1",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "paused-session-1"])

        assert result.exit_code == 0
        record = DovoDb(
            database_file=_paths_for(resume_workspace).database_file, project_id=_paths_for(resume_workspace).project_id
        ).sessions.get("paused-session-1")
        assert record is not None
        assert record.status == SessionStatus.COMPLETED
        assert (resume_workspace / "resumed.marker").exists()

    def test_resume_cli_unknown_session_exits_one(self, cli_runner: CliRunner, resume_workspace: Path) -> None:
        """dovo resume <unknown-id>: no matching paused session, exit 1, 'Resume Failed' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "does-not-exist"])

        assert result.exit_code == 1
        assert "Resume Failed" in result.stdout

    def test_resume_cli_json_format_emits_run_success_event(
        self, cli_runner: CliRunner, resume_workspace: Path
    ) -> None:
        """dovo resume <session_id> --format json: NDJSON stream includes a RunSuccessEvent with payload.status == 'completed'."""
        _seed_paused_session(
            resume_workspace,
            session_id="paused-session-2",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
            ],
            paused_step_id="s2",
        )

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "paused-session-2", "--format", "json"])

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        success_events = [e for e in events if e["event_type"] == "RunSuccessEvent"]
        assert len(success_events) == 1
        assert success_events[0]["payload"]["status"] == "completed"

    def test_resume_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        resume_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-3/integration] dovo resume: malformed User tier config.json → exit 1, tier-attributed message in stdout, no unhandled exception.

        The top-level callback resolves config before the resume handler ever runs, so a
        tier failure here renders a "Config Error" panel, not "Resume Failed" — no paused
        session is ever looked up.
        """
        ui_dispatcher.set_output_format("terminal")
        write_tier_config(ConfigTier.USER, "{not valid json")

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "unknown-session"])

        assert result.exit_code == 1
        assert "Config Error" in result.stdout
        assert "Invalid configuration in user layer" in result.stdout

    def test_resume_cli_completes_after_source_catalog_blueprint_deleted(
        self, cli_runner: CliRunner, resume_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo resume <session_id>: a session paused by dovo run with a uses: step still resumes and completes exit 0 after both the catalog blueprint and step YAML files are deleted from disk."""
        write_runnable_step(
            resume_workspace, key="lint-check", definition={"id": "lint-check", "type": "command", "command": "true"}
        )
        _seed_paused_session(
            resume_workspace,
            session_id="snap-resume-1",
            steps=[
                {"id": "s1", "uses": "lint-check"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
            paused_step_id="s2",
        )
        (resume_workspace / ".dovo" / "catalog" / "blueprints" / "snap-resume-1.yml").unlink()
        (resume_workspace / ".dovo" / "catalog" / "steps" / "lint-check.yml").unlink()

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "snap-resume-1"])

        assert result.exit_code == 0
        record = DovoDb(
            database_file=_paths_for(resume_workspace).database_file, project_id=_paths_for(resume_workspace).project_id
        ).sessions.get("snap-resume-1")
        assert record is not None
        assert record.status == SessionStatus.COMPLETED
        assert (resume_workspace / "resumed.marker").exists()


class ResumeEnvFlagsCliIntegrationTests:
    """Typer runner integration tests for the dovo resume --env-mode and --env-passthrough flags."""

    def test_resume_cli_env_flags_reach_the_resumed_agent_subprocess_env(
        self, cli_runner: CliRunner, resume_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-3/integration] dovo resume: '--env-mode inherit --env-passthrough NAME' on a paused session exits 0 and the FakeAgentRunner env reflects both for the resumed agent step."""
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv("DOVO_TEST_UNRELATED_SECRET", "host-value")
        monkeypatch.setenv("NAME_HOST", "named")
        _seed_paused_agent_session(resume_workspace, "paused-env")

        result = cli_runner.invoke(
            app,
            [
                "-p", str(resume_workspace), "resume", "paused-env",
                "--env-mode", "inherit", "--env-passthrough", "NAME_HOST",
            ],
        )  # fmt: skip

        assert result.exit_code == 0
        assert runner.last_call.env["DOVO_TEST_UNRELATED_SECRET"] == "host-value"
        assert runner.last_call.env["NAME_HOST"] == "named"

    def test_resume_cli_flags_do_not_persist_to_a_later_resume(
        self, cli_runner: CliRunner, resume_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-3/integration] dovo resume: a second resume without flags records an allowlist env lacking the earlier passthrough name."""
        runner = _fake_copilot(monkeypatch)
        monkeypatch.setenv("NAME_HOST", "named")
        _seed_paused_agent_session(resume_workspace, "paused-first")
        _seed_paused_agent_session(resume_workspace, "paused-second")
        config_path = resume_workspace / ".dovo" / "config.json"
        before = config_path.read_bytes()

        first = cli_runner.invoke(
            app, ["-p", str(resume_workspace), "resume", "paused-first", "--env-passthrough", "NAME_HOST"]
        )
        second = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "paused-second"])

        assert (first.exit_code, second.exit_code) == (0, 0)
        assert ["NAME_HOST" in call.env for call in runner.calls] == [True, False]
        assert config_path.read_bytes() == before

    def test_resume_cli_invalid_passthrough_exits_one_and_leaves_session_paused(
        self, cli_runner: CliRunner, resume_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-3/integration] dovo resume: '--env-passthrough =X' exits 1 with the fixed invalid-value message and the session status stays 'paused'."""
        runner = _fake_copilot(monkeypatch)
        _seed_paused_agent_session(resume_workspace, "paused-bad")

        result = cli_runner.invoke(
            app, ["-p", str(resume_workspace), "resume", "paused-bad", "--env-passthrough", "=X"]
        )

        paths = _paths_for(resume_workspace)
        record = DovoDb(database_file=paths.database_file, project_id=paths.project_id).sessions.get("paused-bad")
        assert result.exit_code == 1
        assert "Invalid --env-passthrough value '=X':" in result.stdout
        assert record is not None
        assert record.status == SessionStatus.PAUSED
        assert runner.calls == []
