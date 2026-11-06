"""End-to-end tests for agent steps: the worktree requirement, and live claude runs that make no edit, have an edit rejected, time out, or print an ambient secret."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner, parse_json, write_blueprint

CLAUDE_CREDENTIAL_ENVS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
AMBIENT_SECRET_NAME = "DOVO_E2E_AMBIENT_TOKEN"
AMBIENT_SECRET_VALUE = "e2e-fake-ambient-value-5531"
_WORKTREE_FIX = "Remove '--no-worktree' and run the blueprint with a worktree"


def _claude_unavailable_reason() -> str | None:
    """Return why a live claude agent step cannot run on this machine, or None when it can."""
    if shutil.which("claude") is None:
        return "the claude CLI is not installed or not on PATH"
    if any(os.environ.get(name, "").strip() for name in CLAUDE_CREDENTIAL_ENVS):
        return None
    try:
        status = subprocess.run(
            ["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=30, check=False
        )
        logged_in = status.returncode == 0 and json.loads(status.stdout).get("loggedIn") is True
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        logged_in = False
    return None if logged_in else "the claude CLI is not authenticated"


@pytest.fixture
def claude_project(initialized_project: Path, run_dovo: DovoRunner) -> Path:
    """Return a project whose default agent is claude on the haiku model, skipping when claude cannot run here."""
    reason = _claude_unavailable_reason()
    if reason is not None:
        pytest.skip(f"live agent step needs claude: {reason}")
    for key, value in (("default_agent", "claude"), ("agents.claude.model", "haiku")):
        result = run_dovo(["config", "set", key, value], cwd=initialized_project)
        assert result.exit_code == 0, result.stdout + result.stderr
    subprocess.run(["git", "commit", "-qam", "chore: use claude haiku"], cwd=initialized_project, check=True)
    return initialized_project


def _write_agent_blueprint(project: Path, name: str, step_fields: str, runner: DovoRunner) -> None:
    """Write a one-step blueprint whose only step is an agent step with the given extra YAML fields."""
    write_blueprint(
        project,
        name,
        f"""version: "1.0"
name: {name}
id: {name}
steps:
  - id: agent
    type: agent
{step_fields}""",
        runner=runner,
    )


def _agent_step(run_dovo: DovoRunner, project: Path, session_id: str) -> dict[str, object]:
    """Return the agent step entry of `dovo session show --format json`."""
    show = run_dovo(["session", "show", session_id, "--format", "json"], cwd=project)
    assert show.exit_code == 0, show.stdout + show.stderr
    payload = parse_json(show.stdout)["payload"]
    assert isinstance(payload, dict)
    steps = payload["steps"]
    assert isinstance(steps, list)
    return steps[0]


@pytest.mark.e2e
@pytest.mark.fixture
class AgentStepWorktreeCliTests:
    """E2E tests for the agent step's requirement of a worktree, which fails before any provider runs."""

    def test_agent_step_without_worktree_fails_before_the_agent_runs(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        _write_agent_blueprint(initialized_project, "agent-no-wt", "    prompt: Reply with the word done.\n", run_dovo)

        result = run_dovo(
            ["run", "agent-no-wt", "--no-worktree", "--no-tty", "--session-id", "sess-agent-no-wt"],
            cwd=initialized_project,
        )

        assert result.exit_code == 1
        assert "Agent steps require an active git worktree.\nFix:\n- " + _WORKTREE_FIX in result.stdout
        step = _agent_step(run_dovo, initialized_project, "sess-agent-no-wt")
        assert (step["state"], step["exit_code"], step["error_code"]) == ("failed", 203, "STEP_WORKTREE_MISSING")
        assert (step["error_message"], step["remediations"]) == (
            "Agent steps require an active git worktree.",
            [{"message": _WORKTREE_FIX, "command": None}],
        )

    def test_agent_step_without_worktree_reports_remediations_in_json(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        _write_agent_blueprint(initialized_project, "agent-no-wt", "    prompt: Reply with the word done.\n", run_dovo)

        result = run_dovo(
            ["run", "agent-no-wt", "--no-worktree", "--no-tty", "--session-id", "sess-agent-json", "--format", "json"],
            cwd=initialized_project,
        )

        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        step_done = next(event["payload"] for event in events if event["event_type"] == "run.step_done")
        step = _agent_step(run_dovo, initialized_project, "sess-agent-json")
        assert result.exit_code == 1
        assert step_done["error_message"] == "Agent steps require an active git worktree."
        assert step_done["remediations"] == [{"message": _WORKTREE_FIX, "command": None}]
        assert step["remediations"] == step_done["remediations"]


@pytest.mark.e2e
@pytest.mark.fixture
@pytest.mark.llm
class AgentStepLiveCliTests:
    """E2E tests running agent steps through the claude provider on the haiku model."""

    def test_agent_step_that_makes_no_edit_completes_as_no_op(self, run_dovo: DovoRunner, claude_project: Path) -> None:
        _write_agent_blueprint(
            claude_project,
            "agent-no-edit",
            "    prompt: Do not read, create or change any file. Reply with the word done.\n",
            run_dovo,
        )

        result = run_dovo(
            ["run", "agent-no-edit", "--no-tty", "--session-id", "sess-agent-no-edit"], cwd=claude_project, timeout=180
        )

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Result: no_op" in result.stdout
        assert "status: completed" in result.stdout
        step = _agent_step(run_dovo, claude_project, "sess-agent-no-edit")
        assert (step["state"], step["exit_code"]) == ("completed", 0)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=claude_project, capture_output=True, text=True)
        assert status.stdout == ""

    def test_agent_edit_over_max_files_is_rejected_saved_and_discarded(
        self, run_dovo: DovoRunner, claude_project: Path
    ) -> None:
        _write_agent_blueprint(
            claude_project,
            "agent-reject",
            "    max_files: 1\n    tools:\n      allow_all: true\n"
            "    prompt: Create two files, one.txt and two.txt, each containing the line x. Do nothing else.\n",
            run_dovo,
        )

        result = run_dovo(
            ["run", "agent-reject", "--no-tty", "--session-id", "sess-agent-reject"], cwd=claude_project, timeout=180
        )

        assert result.exit_code == 1, result.stdout + result.stderr
        assert "Patch touches 2 files; max_files is 1." in result.stdout
        step = _agent_step(run_dovo, claude_project, "sess-agent-reject")
        assert (step["state"], step["exit_code"], step["error_code"]) == ("failed", 203, "PATCH_FILE_COUNT_EXCEEDED")
        saved_patch = Path(str(step["rejected_patch_path"]))
        assert saved_patch.name == "agent_attempt_1.patch"
        assert "one.txt" in saved_patch.read_text(encoding="utf-8")
        assert "two.txt" in saved_patch.read_text(encoding="utf-8")
        kept_worktree = claude_project / ".dovo" / "worktrees" / "sess-agent-reject"
        assert kept_worktree.is_dir()
        assert not (kept_worktree / "one.txt").exists()
        assert not (kept_worktree / "two.txt").exists()

    def test_agent_step_past_timeout_fails_as_timed_out(self, run_dovo: DovoRunner, claude_project: Path) -> None:
        _write_agent_blueprint(
            claude_project,
            "agent-timeout",
            "    timeout_seconds: 3\n    tools:\n      allow_all: true\n"
            "    prompt: Run the shell command `python3 -c 'import time; time.sleep(60)'` and wait for it to finish.\n",
            run_dovo,
        )

        result = run_dovo(
            ["run", "agent-timeout", "--no-tty", "--session-id", "sess-agent-timeout"], cwd=claude_project, timeout=60
        )

        assert result.exit_code == 1, result.stdout + result.stderr
        assert "Agent timed out after 3s (provider=claude)." in result.stdout
        assert "Raise 'timeout_seconds' on the agent step" in result.stdout
        step = _agent_step(run_dovo, claude_project, "sess-agent-timeout")
        assert (step["state"], step["exit_code"], step["error_code"]) == ("failed", 202, "AGENT_TIMEOUT")

    def test_failed_step_with_withheld_variables_carries_the_passthrough_fix_as_a_remediation(
        self, run_dovo: DovoRunner, claude_project: Path
    ) -> None:
        _write_agent_blueprint(
            claude_project,
            "agent-withheld",
            "    timeout_seconds: 3\n    tools:\n      allow_all: true\n"
            "    prompt: Run the shell command `python3 -c 'import time; time.sleep(60)'` and wait for it to finish.\n",
            run_dovo,
        )

        result = run_dovo(
            ["run", "agent-withheld", "--no-tty", "--session-id", "sess-agent-withheld", "--format", "json"],
            cwd=claude_project,
            env={AMBIENT_SECRET_NAME: AMBIENT_SECRET_VALUE},
            timeout=60,
        )

        output = result.stdout + result.stderr
        step = _agent_step(run_dovo, claude_project, "sess-agent-withheld")
        assert result.exit_code == 1, output
        assert "Agent environment filtered: " in output
        assert "Fix: add names to" not in output
        remediations = step["remediations"]
        assert isinstance(remediations, list)
        assert (step["error_code"], remediations[-1]) == (
            "AGENT_TIMEOUT",
            {"message": "Add names to agents.claude.env_passthrough or pass --env-passthrough NAME", "command": None},
        )

    def test_ambient_secret_printed_by_agent_is_redacted_in_output_and_logs(
        self, run_dovo: DovoRunner, claude_project: Path
    ) -> None:
        _write_agent_blueprint(
            claude_project,
            "agent-redact",
            "    tools:\n      allow_all: true\n"
            f"    prompt: Run the shell command `printenv {AMBIENT_SECRET_NAME}` and reply with its exact output.\n",
            run_dovo,
        )
        secret_env = {AMBIENT_SECRET_NAME: AMBIENT_SECRET_VALUE}
        mask = f"[REDACTED:{AMBIENT_SECRET_NAME}]"

        result = run_dovo(
            [
                "run",
                "agent-redact",
                "--no-tty",
                "--session-id",
                "sess-agent-redact",
                "--env-passthrough",
                AMBIENT_SECRET_NAME,
            ],
            cwd=claude_project,
            env=secret_env,
            timeout=180,
        )

        assert result.exit_code == 0, result.stdout + result.stderr
        assert mask in result.stdout
        assert AMBIENT_SECRET_VALUE not in result.stdout + result.stderr
        step_log = run_dovo(
            ["logs", "sess-agent-redact", "--step", "agent", "--stream", "stdout"], cwd=claude_project, env=secret_env
        )
        assert step_log.exit_code == 0
        assert mask in step_log.stdout
        assert AMBIENT_SECRET_VALUE not in step_log.stdout
        show = run_dovo(["session", "show", "sess-agent-redact", "--logs"], cwd=claude_project, env=secret_env)
        assert show.exit_code == 0
        assert AMBIENT_SECRET_VALUE not in show.stdout
        for written in (*DOVO_HOME.rglob("*"), *claude_project.rglob("*")):
            if written.is_file() and ".git" not in written.parts:
                assert AMBIENT_SECRET_VALUE not in written.read_text(encoding="utf-8", errors="replace"), written
