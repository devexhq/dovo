"""End-to-end tests for blueprint checkpointing and resumption lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from e2e.conftest import DovoPtyRunner, DovoRunner


def _write_blueprint(project: Path, name: str, content: str, *, runner: DovoRunner | None = None) -> Path:
    """Write a blueprint YAML definition into the project repository catalog and commit it."""
    blueprint_file = project / ".dovo" / "catalog" / "blueprints" / f"{name}.yml"
    blueprint_file.parent.mkdir(parents=True, exist_ok=True)
    blueprint_file.write_text(content, encoding="utf-8")
    if runner is not None:
        runner(["blueprint", "list"], cwd=project)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: add blueprint {name}"], cwd=project, check=True, capture_output=True
    )
    return blueprint_file


def _parse_json(stdout: str) -> dict[str, object]:
    """Extract and parse first JSON payload from command output."""
    for line in stdout.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return json.loads(stdout)


@pytest.mark.e2e
@pytest.mark.fixture
class CheckpointAndResumeCliTests:
    """E2E tests for durable checkpointing, pausing, and resuming blueprint execution."""

    def test_failing_blueprint_saves_resumable_checkpoint(
        self,
        run_dovo: DovoRunner,
        run_dovo_pty: DovoPtyRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: A failing deterministic blueprint saves a resumable checkpoint in database and state store.

        Given an initialized project containing a blueprint step configured with on_failure: prompt_user that fails
        When dovo run is executed interactively and interrupted while waiting for prompt decision
        Then a resumable checkpoint is saved with status paused in database, state store, and history
        """
        _write_blueprint(
            initialized_project,
            "resumable-check-bp",
            """version: "1.0"
name: resumable-check-bp
id: resumable-check-bp
steps:
  - id: step-one
    run: echo "step-one done"
  - id: step-two
    run: test -f marker.txt
    on_failure: prompt_user
""",
            runner=run_dovo,
        )

        session_id = "paused-sess-checkpoint"
        pty_result = run_dovo_pty(
            ["run", "resumable-check-bp", "--session-id", session_id, "--no-worktree"],
            prompt_replies=[("Select option [r/c/a]: ", "\x03")],
            cwd=initialized_project,
        )

        assert pty_result.exit_code == 130
        assert "Step 'step-two' failed" in pty_result.transcript
        assert "Select option [r/c/a]:" in pty_result.transcript

        history_result = run_dovo(["history", "show", session_id, "--format", "json"], cwd=initialized_project)
        assert history_result.exit_code == 0
        history_data = _parse_json(history_result.stdout)
        session_payload = history_data.get("payload", {})
        assert isinstance(session_payload, dict)
        session_record = session_payload.get("session", {})
        assert isinstance(session_record, dict)
        assert session_record.get("status") == "paused"
        assert session_record.get("session_id") == session_id

        dovo_home = Path(os.environ.get("DOVO_HOME", "/tmp/dovo-home"))
        matching_state_files = list(dovo_home.glob(f"**/sessions/{session_id}/session.json"))
        assert len(matching_state_files) == 1
        assert matching_state_files[0].is_file()

        state_content = json.loads(matching_state_files[0].read_text(encoding="utf-8"))
        nodes = state_content.get("nodes", [])
        assert len(nodes) == 2
        assert nodes[0].get("state") == "completed"
        assert nodes[1].get("state") == "paused"

    def test_resume_without_arguments_completes_latest_paused_checkpoint(
        self,
        run_dovo: DovoRunner,
        run_dovo_pty: DovoPtyRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: After a controlled correction, dovo resume without arguments finds and completes the latest paused checkpoint.

        Given a paused blueprint session waiting on an unmet prerequisite condition
        When the prerequisite is corrected and dovo resume is executed without arguments
        Then the latest paused session is resumed, step retries successfully, downstream steps complete, and status transitions to completed
        """
        _write_blueprint(
            initialized_project,
            "resumable-default-bp",
            """version: "1.0"
name: resumable-default-bp
id: resumable-default-bp
steps:
  - id: step-first
    run: echo "first step ok"
  - id: step-needs-fix
    run: test -f fix_marker.txt
    on_failure: prompt_user
  - id: step-final
    run: echo "final step completed"
""",
            runner=run_dovo,
        )

        session_id = "paused-sess-latest"
        run_result = run_dovo_pty(
            ["run", "resumable-default-bp", "--session-id", session_id, "--no-worktree"],
            prompt_replies=[("Select option [r/c/a]: ", "\x03")],
            cwd=initialized_project,
        )
        assert run_result.exit_code == 130

        before_resume = run_dovo(["history", "show", session_id, "--format", "json"], cwd=initialized_project)
        before_data = _parse_json(before_resume.stdout)
        session_info = before_data.get("payload", {}).get("session", {})  # pyright: ignore[reportAttributeAccessIssue]
        assert session_info.get("status") == "paused"

        (initialized_project / "fix_marker.txt").write_text("fixed", encoding="utf-8")

        resume_result = run_dovo_pty(
            ["resume"],
            prompt_replies=[("Select option [r/c/a]: ", "r\n")],
            cwd=initialized_project,
        )

        assert resume_result.exit_code == 0
        assert f"Resuming latest paused session '{session_id}'" in resume_result.transcript
        assert "Executing step-needs-fix" in resume_result.transcript
        assert "step-needs-fix COMPLETED" in resume_result.transcript
        assert "final step completed" in resume_result.transcript
        assert "Blueprint Run Completed: resumable-default-bp" in resume_result.transcript

        after_resume = run_dovo(["history", "show", session_id, "--format", "json"], cwd=initialized_project)
        assert after_resume.exit_code == 0
        after_data = _parse_json(after_resume.stdout)
        after_session = after_data.get("payload", {}).get("session", {})  # pyright: ignore[reportAttributeAccessIssue]
        assert after_session.get("status") == "completed"

    def test_resume_with_explicit_session_id_completes_targeted_session(
        self,
        run_dovo: DovoRunner,
        run_dovo_pty: DovoPtyRunner,
        initialized_project: Path,
    ) -> None:
        """Scenario: dovo resume with an explicit session identifier resumes and completes the targeted paused session.

        Given multiple sessions including a specific paused session
        When the prerequisite is corrected and dovo resume is executed targeting the explicit session identifier
        Then the targeted session executes to completion and records completed status in history
        """
        _write_blueprint(
            initialized_project,
            "resumable-targeted-bp",
            """version: "1.0"
name: resumable-targeted-bp
id: resumable-targeted-bp
steps:
  - id: step-one
    run: echo "initial step ok"
  - id: step-targeted-check
    run: test -f targeted_fix.txt
    on_failure: prompt_user
""",
            runner=run_dovo,
        )

        session_id = "paused-sess-targeted"
        run_result = run_dovo_pty(
            ["run", "resumable-targeted-bp", "--session-id", session_id, "--no-worktree"],
            prompt_replies=[("Select option [r/c/a]: ", "\x03")],
            cwd=initialized_project,
        )
        assert run_result.exit_code == 130

        (initialized_project / "targeted_fix.txt").write_text("targeted ok", encoding="utf-8")

        resume_result = run_dovo_pty(
            ["resume", session_id],
            prompt_replies=[("Select option [r/c/a]: ", "r\n")],
            cwd=initialized_project,
        )

        assert resume_result.exit_code == 0
        assert f"Resuming session '{session_id}'" in resume_result.transcript
        assert "step-targeted-check COMPLETED" in resume_result.transcript
        assert "Blueprint Run Completed: resumable-targeted-bp" in resume_result.transcript

        show_result = run_dovo(["history", "show", session_id, "--format", "json"], cwd=initialized_project)
        assert show_result.exit_code == 0
        show_data = _parse_json(show_result.stdout)
        session_info = show_data.get("payload", {}).get("session", {})  # pyright: ignore[reportAttributeAccessIssue]
        assert session_info.get("status") == "completed"
