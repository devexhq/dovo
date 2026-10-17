"""End-to-end tests for blueprint execution (`dovo run`) lifecycle, isolation, and parameters."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner, find_event, write_blueprint


def _get_project_id(project: Path) -> str:
    """Extract project identifier from .dovo/project.json."""
    data = json.loads((project / ".dovo" / "project.json").read_text(encoding="utf-8"))
    return str(data.get("id", ""))


@pytest.mark.e2e
@pytest.mark.fixture
class RunCliTests:
    """E2E tests for blueprint execution lifecycle, isolation, and parameter handling."""

    def test_run_auto_initializes_uninitialized_git_repo(self, run_dovo: DovoRunner, git_project: Path) -> None:
        assert not (git_project / ".dovo" / "project.json").exists()

        global_bp_dir = DOVO_HOME / "global" / "catalog" / "blueprints"
        global_bp_dir.mkdir(parents=True, exist_ok=True)
        global_bp_file = global_bp_dir / "lazy-init-bp.yml"
        global_bp_file.write_text(
            """version: "1.0"
name: lazy-init-bp
id: lazy-init-bp
steps:
  - id: step-1
    run: echo "lazy initialized" > lazy_output.txt
""",
            encoding="utf-8",
        )

        result = run_dovo(["run", "lazy-init-bp", "--no-worktree"], cwd=git_project)

        assert result.exit_code == 0
        assert (git_project / ".dovo" / "project.json").is_file()
        assert (git_project / ".dovo" / "config.json").is_file()
        assert (git_project / "lazy_output.txt").read_text().strip() == "lazy initialized"

    def test_run_no_worktree_persists_completed_session(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        write_blueprint(
            initialized_project,
            "deterministic-bp",
            """version: "1.0"
name: deterministic-bp
id: deterministic-bp
steps:
  - id: write-file
    run: echo "deterministic result" > det_out.txt
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "deterministic-bp", "--no-worktree", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("status") == "completed"

        assert (initialized_project / "det_out.txt").read_text().strip() == "deterministic result"

        session_id = str(payload.get("session_id", ""))
        assert session_id

        hist_res = run_dovo(["session", "list", "--format", "json"], cwd=initialized_project)
        assert hist_res.exit_code == 0
        hist_payload = json.loads(hist_res.stdout).get("payload", {})
        sessions = hist_payload.get("sessions", [])
        matching = [s for s in sessions if s.get("session_id") == session_id]
        assert len(matching) == 1
        assert matching[0].get("status") == "completed"
        assert matching[0].get("blueprint_name") == "deterministic-bp"

        project_id = _get_project_id(initialized_project)
        session_dir = DOVO_HOME / "storage" / "projects" / project_id / "sessions" / session_id
        assert session_dir.is_dir()
        assert (session_dir / "session.json").is_file()

    def test_run_worktree_creates_branch_directory_and_run_symlink(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        session_id = "worktree-sess-01"
        write_blueprint(
            initialized_project,
            "worktree-check-bp",
            """version: "1.0"
name: worktree-check-bp
id: worktree-check-bp
steps:
  - id: verify-worktree-env
    run: |
      test -L .dovo/run && echo "symlink_ok" > symlink_check.txt
      git branch --show-current > current_branch.txt
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "worktree-check-bp", "--keep", "--session-id", session_id, "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        assert envelope.get("event_type") == "RunSuccessEvent"

        worktree_dir = initialized_project / ".dovo" / "worktrees" / session_id
        assert worktree_dir.is_dir()

        run_symlink = worktree_dir / ".dovo" / "run"
        assert run_symlink.is_symlink()
        assert run_symlink.is_dir()
        assert run_symlink.resolve().name == session_id
        assert (run_symlink / "session.json").is_file()

        assert (worktree_dir / "symlink_check.txt").read_text().strip() == "symlink_ok"
        assert (worktree_dir / "current_branch.txt").read_text().strip() == f"dovo/{session_id}"

        branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{session_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert f"dovo/{session_id}" in branch_proc.stdout

    def test_run_auto_apply_transfers_worktree_changes_to_workspace(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "auto-apply-bp",
            """version: "1.0"
name: auto-apply-bp
id: auto-apply-bp
steps:
  - id: generate-file
    run: echo "auto-applied artifact content" > applied_file.txt
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "auto-apply-bp", "--auto-apply", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        assert envelope.get("event_type") == "RunSuccessEvent"

        applied_file = initialized_project / "applied_file.txt"
        assert applied_file.is_file()
        assert applied_file.read_text().strip() == "auto-applied artifact content"

    def test_run_keep_retains_worktree_and_branch_on_disk(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Without --keep the worktree and branch are removed; with --keep both are retained."""
        write_blueprint(
            initialized_project,
            "keep-toggle-bp",
            """version: "1.0"
name: keep-toggle-bp
id: keep-toggle-bp
steps:
  - id: step-1
    run: echo "keep test step"
""",
            runner=run_dovo,
        )

        clean_id = "sess-without-keep"
        clean_result = run_dovo(
            ["run", "keep-toggle-bp", "--session-id", clean_id, "--format", "json"],
            cwd=initialized_project,
        )
        assert clean_result.exit_code == 0
        assert not (initialized_project / ".dovo" / "worktrees" / clean_id).exists()
        clean_branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{clean_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert f"dovo/{clean_id}" not in clean_branch_proc.stdout

        kept_id = "sess-with-keep"
        kept_result = run_dovo(
            ["run", "keep-toggle-bp", "--keep", "--session-id", kept_id, "--format", "json"],
            cwd=initialized_project,
        )
        assert kept_result.exit_code == 0
        assert (initialized_project / ".dovo" / "worktrees" / kept_id).is_dir()
        kept_branch_proc = subprocess.run(
            ["git", "branch", "--list", f"dovo/{kept_id}"],
            cwd=initialized_project,
            capture_output=True,
            text=True,
            check=True,
        )
        assert f"dovo/{kept_id}" in kept_branch_proc.stdout

    def test_run_session_id_assigns_explicit_identifier(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        explicit_id = "explicit-sess-99"
        write_blueprint(
            initialized_project,
            "explicit-id-bp",
            """version: "1.0"
name: explicit-id-bp
id: explicit-id-bp
steps:
  - id: step-1
    run: echo "explicit id step"
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "explicit-id-bp", "--no-worktree", "--session-id", explicit_id, "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("session_id") == explicit_id

        hist_res = run_dovo(["session", "list", "--format", "json"], cwd=initialized_project)
        assert hist_res.exit_code == 0
        hist_payload = json.loads(hist_res.stdout).get("payload", {})
        sessions = hist_payload.get("sessions", [])
        matching = [s for s in sessions if s.get("session_id") == explicit_id]
        assert len(matching) == 1
        assert matching[0].get("blueprint_name") == "explicit-id-bp"
        assert matching[0].get("status") == "completed"

        project_id = _get_project_id(initialized_project)
        session_dir = DOVO_HOME / "storage" / "projects" / project_id / "sessions" / explicit_id
        assert session_dir.is_dir()
        assert (session_dir / "session.json").is_file()

    def test_run_parameter_forwarding_interpolates_inputs(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "param-forwarding-bp",
            """version: "1.0"
name: param-forwarding-bp
id: param-forwarding-bp
inputs:
  msg:
    type: string
    aliases:
      - "--msg"
steps:
  - id: write-msg
    run: echo "{{ inputs.msg }}" > msg.txt
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "param-forwarding-bp", "--no-worktree", "--msg", "forwarded-parameter-value"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        assert (initialized_project / "msg.txt").read_text().strip() == "forwarded-parameter-value"

    def test_run_missing_required_input_aborts_without_session(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "required-input-bp",
            """version: "1.0"
name: required-input-bp
id: required-input-bp
inputs:
  required_param:
    type: string
    required: true
    aliases:
      - "--required-param"
steps:
  - id: step-1
    run: echo "{{ inputs.required_param }}"
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "required-input-bp", "--no-worktree"],
            cwd=initialized_project,
        )

        assert result.exit_code == 1
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "Missing required input 'required_param'" in combined_output

        hist_res = run_dovo(["session", "list", "--format", "json"], cwd=initialized_project)
        assert hist_res.exit_code == 0
        hist_payload = json.loads(hist_res.stdout).get("payload", {})
        assert hist_payload.get("total_sessions") == 0
        assert hist_payload.get("sessions") == []

        project_id = _get_project_id(initialized_project)
        sessions_dir = DOVO_HOME / "storage" / "projects" / project_id / "sessions"
        assert not sessions_dir.exists() or not any(sessions_dir.iterdir())
