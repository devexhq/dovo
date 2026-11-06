"""End-to-end tests for blueprint execution (`dovo run`) lifecycle, isolation, and parameters."""

from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

import pytest

from dovo.common.process import is_process_group_alive
from e2e.conftest import (
    DOVO_HOME,
    DovoProcessStarter,
    DovoRunner,
    find_event,
    parse_events,
    parse_json,
    run_result,
    write_blueprint,
)
from tests.harness.poll import wait_until


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

    def test_run_json_failed_auto_init_writes_one_error_event(self, run_dovo: DovoRunner, git_project: Path) -> None:
        (git_project / ".dovo").write_text("not a directory\n", encoding="utf-8")

        result = run_dovo(["run", "anything", "--format", "json"], cwd=git_project)

        assert result.exit_code == 1
        envelope = parse_json(result.stdout)
        assert envelope.get("event_type") == "error"
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("error_code") == "BOOTSTRAP_PATH_INVALID"
        assert payload.get("errors")
        assert payload.get("remediations")

    def test_run_failed_auto_init_prints_the_error_panel(self, run_dovo: DovoRunner, git_project: Path) -> None:
        (git_project / ".dovo").write_text("not a directory\n", encoding="utf-8")

        result = run_dovo(["run", "anything"], cwd=git_project)

        assert result.exit_code == 1
        assert "Initialization Failed!" in result.stdout

    def test_run_ignores_an_undeclared_option_with_a_warning(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "undeclared-option-bp",
            """version: "1.0"
name: undeclared-option-bp
id: undeclared-option-bp
steps:
  - id: step-1
    run: "true"
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "undeclared-option-bp", "--no-worktree", "--bogus", "1", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        assert run_result(result.stdout).get("warnings") == [
            "Ignoring unrecognized option '--bogus'.",
            "Ignoring unexpected argument '1'.",
        ]

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
        payload = run_result(result.stdout)
        assert (payload.get("ok"), payload.get("session_status")) == (True, "completed")

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
        payload = run_result(result.stdout)
        assert (payload.get("ok"), payload.get("session_status")) == (True, "completed")

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
        payload = run_result(result.stdout)
        assert (payload.get("ok"), payload.get("session_status")) == (True, "completed")

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
        payload = run_result(result.stdout)
        assert (payload.get("ok"), payload.get("session_id")) == (True, explicit_id)

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


@pytest.mark.e2e
@pytest.mark.fixture
class RunInterruptCliTests:
    """E2E tests for `dovo run` receiving SIGINT, SIGTERM or SIGHUP while a step runs."""

    @pytest.mark.parametrize(
        "signum",
        [
            pytest.param(signal.SIGINT, id="sigint"),
            pytest.param(signal.SIGTERM, id="sigterm"),
            pytest.param(signal.SIGHUP, id="sighup"),
        ],
    )
    def test_run_interrupted_during_step_records_cancelled_and_stops_step_group(
        self,
        start_dovo: DovoProcessStarter,
        run_dovo: DovoRunner,
        initialized_project: Path,
        tmp_path: Path,
        signum: int,
    ) -> None:
        """Signal `dovo run` once its step wrote its PGID, then see returncode -signum, "Run Cancelled" in stdout, `session show` status cancelled, and no process in that group within 5 s."""
        pid_file = tmp_path / "step.pid"
        write_blueprint(
            initialized_project,
            "interrupt-bp",
            f"""version: "1.0"
name: interrupt-bp
id: interrupt-bp
steps:
  - id: long-step
    run: echo $$ > '{pid_file}' && exec sleep 300
""",
            runner=run_dovo,
        )
        session_id = f"interrupt-{signum}"

        process = start_dovo(["run", "interrupt-bp", "--session-id", session_id], cwd=initialized_project)
        assert wait_until(lambda: pid_file.exists() and pid_file.read_text().endswith("\n"), timeout=10)
        pgid = int(pid_file.read_text())
        process.send_signal(signum)
        stdout, _ = process.communicate(timeout=15)

        assert process.returncode == -signum
        assert "Run Cancelled" in stdout
        assert wait_until(lambda: not is_process_group_alive(pgid), timeout=5.0)
        show = run_dovo(["session", "show", session_id, "--format", "json"], cwd=initialized_project)
        payload = parse_json(show.stdout).get("payload", {})
        assert isinstance(payload, dict)
        session_record = payload.get("session", {})
        assert isinstance(session_record, dict)
        assert session_record.get("status") == "cancelled"

    def test_run_hangup_releases_the_lock_for_the_next_command(
        self,
        start_dovo: DovoProcessStarter,
        run_dovo: DovoRunner,
        initialized_project: Path,
        tmp_path: Path,
    ) -> None:
        """SIGHUP `dovo run` once its step wrote its PGID; a following `dovo run` with a 1 s lock timeout exits 0 and reports no lock wait."""
        pid_file = tmp_path / "step.pid"
        _write_step_blueprint(run_dovo, initialized_project, "hangup-bp", f"echo $$ > '{pid_file}' && exec sleep 300")
        _write_step_blueprint(run_dovo, initialized_project, "quick-bp", "true")

        process = start_dovo(["run", "hangup-bp", "--session-id", "hangup-1"], cwd=initialized_project)
        assert wait_until(lambda: pid_file.exists() and pid_file.read_text().endswith("\n"), timeout=10)
        process.send_signal(signal.SIGHUP)
        process.communicate(timeout=15)
        following = run_dovo(
            ["run", "quick-bp", "--no-worktree"], cwd=initialized_project, env={"DOVO_LOCK_TIMEOUT_SECONDS": "1"}
        )

        assert process.returncode == -signal.SIGHUP
        assert following.exit_code == 0, following.stdout + following.stderr
        assert "Workspace lock is currently held" not in f"{following.stdout}{following.stderr}"

    def test_run_interrupted_step_ignoring_sigterm_is_cancelled_only_after_it_is_gone(
        self,
        start_dovo: DovoProcessStarter,
        run_dovo: DovoRunner,
        initialized_project: Path,
        tmp_path: Path,
    ) -> None:
        """SIGINT `dovo run` while its step ignores SIGTERM; once dovo has exited, no process of the step's group is alive and the session reads cancelled."""
        pid_file = tmp_path / "step.pid"
        _write_step_blueprint(
            run_dovo, initialized_project, "stubborn-bp", f"trap '' TERM; echo $$ > '{pid_file}'; exec sleep 300"
        )

        process = start_dovo(["run", "stubborn-bp", "--session-id", "stubborn-1"], cwd=initialized_project)
        assert wait_until(lambda: pid_file.exists() and pid_file.read_text().endswith("\n"), timeout=10)
        pgid = int(pid_file.read_text())
        process.send_signal(signal.SIGINT)
        stdout, _ = process.communicate(timeout=15)

        assert process.returncode == -signal.SIGINT
        assert not is_process_group_alive(pgid)
        assert "Run Cancelled" in stdout
        assert _session_status(run_dovo, initialized_project, "stubborn-1") == "cancelled"

    def test_run_interrupted_during_worktree_setup_prints_its_run_result(
        self,
        start_dovo: DovoProcessStarter,
        run_dovo: DovoRunner,
        initialized_project: Path,
        tmp_path: Path,
    ) -> None:
        """SIGINT `dovo run --format json` while a post-checkout hook delays the worktree creation; the last line is a run result naming the cancelled session, and `session show` reads cancelled."""
        marker = tmp_path / "hook-started"
        hook = initialized_project / ".git" / "hooks" / "post-checkout"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nsleep 4\n", encoding="utf-8")
        hook.chmod(0o755)
        _write_step_blueprint(run_dovo, initialized_project, "setup-bp", "true")

        process = start_dovo(
            ["run", "setup-bp", "--session-id", "setup-1", "--format", "json"], cwd=initialized_project
        )
        assert wait_until(marker.exists, timeout=10)
        process.send_signal(signal.SIGINT)
        stdout, _ = process.communicate(timeout=15)

        payload = run_result(stdout)
        assert process.returncode == -signal.SIGINT
        assert (payload["error_code"], payload["session_id"], payload["session_status"]) == (
            "RUN_CANCELLED",
            "setup-1",
            "cancelled",
        )
        assert _session_status(run_dovo, initialized_project, "setup-1") == "cancelled"

    def test_run_interrupted_inside_loop_records_loop_cancelled(
        self,
        start_dovo: DovoProcessStarter,
        run_dovo: DovoRunner,
        initialized_project: Path,
        tmp_path: Path,
    ) -> None:
        """SIGINT `dovo run` while a loop body step sleeps; `session show` lists no row running and the loop row cancelled."""
        pid_file = tmp_path / "body.pid"
        write_blueprint(
            initialized_project,
            "loop-bp",
            f"""version: "1.0"
name: loop-bp
id: loop-bp
steps:
  - id: loop
    type: loop
    max_iterations: 2
    until:
      - "iteration.index >= 2"
    do:
      - id: body
        run: echo $$ > '{pid_file}' && exec sleep 20
""",
            runner=run_dovo,
        )

        process = start_dovo(["run", "loop-bp", "--no-worktree", "--session-id", "loop-1"], cwd=initialized_project)
        assert wait_until(lambda: pid_file.exists() and pid_file.read_text().endswith("\n"), timeout=10)
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=15)

        show = run_dovo(["session", "show", "loop-1", "--format", "json"], cwd=initialized_project)
        payload = parse_json(show.stdout).get("payload", {})
        assert isinstance(payload, dict)
        steps = payload.get("steps", [])
        assert isinstance(steps, list)
        assert process.returncode == -signal.SIGINT
        assert "running" not in [step.get("state") for step in steps]
        assert [step.get("state") for step in steps if step.get("step_id") == "loop"] == ["cancelled"]


@pytest.mark.e2e
@pytest.mark.fixture
class RunStepErrorCliTests:
    """E2E tests for a step whose execution fails with an unexpected error."""

    def test_non_utf8_step_output_fails_the_step_and_names_the_session(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """A step writing non-UTF-8 bytes to $DOVO_OUTPUT fails with INTERNAL_ERROR, the run result names the failed session, and `dovo resume` does not run the step again."""
        _write_step_blueprint(
            run_dovo, initialized_project, "bad-output-bp", "printf 'K=\\377\\376\\n' > \"$DOVO_OUTPUT\""
        )

        result = run_dovo(
            ["run", "bad-output-bp", "--no-worktree", "--session-id", "bad-1", "--format", "json"],
            cwd=initialized_project,
        )
        resumed = run_dovo(["resume", "bad-1", "--format", "json"], cwd=initialized_project)

        step_done = find_event(result.stdout, "run.step_done").get("payload", {})
        assert isinstance(step_done, dict)
        payload = run_result(result.stdout)
        assert result.exit_code == 1
        assert (step_done.get("ok"), step_done.get("error_code")) == (False, "INTERNAL_ERROR")
        assert (payload["session_id"], payload["blueprint_name"], payload["session_status"]) == (
            "bad-1",
            "bad-output-bp",
            "failed",
        )
        assert _session_status(run_dovo, initialized_project, "bad-1") == "failed"
        assert "run.step_start" not in [event.get("event_type") for event in parse_events(resumed.stdout)]


def _write_step_blueprint(run_dovo: DovoRunner, project: Path, name: str, command: str) -> None:
    """Write a blueprint named name whose single step runs command."""
    write_blueprint(
        project,
        name,
        f"""version: "1.0"
name: {name}
id: {name}
steps:
  - id: only-step
    run: {json.dumps(command)}
""",
        runner=run_dovo,
    )


def _session_status(run_dovo: DovoRunner, project: Path, session_id: str) -> object:
    """Return the status `dovo session show --format json` reports for session_id."""
    show = run_dovo(["session", "show", session_id, "--format", "json"], cwd=project)
    payload = parse_json(show.stdout).get("payload", {})
    assert isinstance(payload, dict)
    session_record = payload.get("session", {})
    assert isinstance(session_record, dict)
    return session_record.get("status")
