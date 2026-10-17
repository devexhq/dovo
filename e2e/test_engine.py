"""End-to-end tests for engine orchestration, failure policies, loops, and control."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e.conftest import DovoPtyRunner, DovoRunner, find_event, write_blueprint


@pytest.mark.e2e
@pytest.mark.fixture
class EngineOrchestrationCliTests:
    """E2E tests for engine step assertions, failure policies, loops, and process timeouts."""

    def test_step_assert_fails_when_assertions_fail(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        write_blueprint(
            initialized_project,
            "assert-fail-bp",
            """version: "1.0"
name: assert-fail-bp
id: assert-fail-bp
steps:
  - id: failing-assert-step
    run: echo "actual output"
    assert:
      exit_code: 0
      file_exists: "missing_file.txt"
      output_contains: "missing expected text"
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "assert-fail-bp", "--no-worktree", "--no-tty"],
            cwd=initialized_project,
        )

        assert result.exit_code == 1
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "failed assertion checks" in combined_output
        assert "missing_file.txt" in combined_output
        assert "missing expected text" in combined_output

    def test_step_on_failure_continue_records_ignored_and_completes_downstream(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "on-failure-continue-bp",
            """version: "1.0"
name: on-failure-continue-bp
id: on-failure-continue-bp
steps:
  - id: failing-step
    run: exit 1
    on_failure: continue
  - id: downstream-step
    run: echo "downstream executed" > downstream.txt
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "on-failure-continue-bp", "--no-worktree", "--session-id", "sess-continue", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("status") == "completed"

        downstream_file = initialized_project / "downstream.txt"
        assert downstream_file.is_file()
        assert downstream_file.read_text().strip() == "downstream executed"

    def test_step_on_failure_retry_retries_and_succeeds(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        write_blueprint(
            initialized_project,
            "on-failure-retry-bp",
            """version: "1.0"
name: on-failure-retry-bp
id: on-failure-retry-bp
steps:
  - id: retry-step
    run: |
      if [ -f flag.txt ]; then
        echo "success on retry" > success.txt
        exit 0
      else
        touch flag.txt
        exit 1
      fi
    on_failure:
      action: retry
      max_retries: 2
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "on-failure-retry-bp", "--no-worktree", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("status") == "completed"

        success_file = initialized_project / "success.txt"
        assert success_file.is_file()
        assert success_file.read_text().strip() == "success on retry"

    def test_prompt_user_interactive_pty_retry_completes_run(
        self, run_dovo_pty: DovoPtyRunner, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "prompt-retry-bp",
            """version: "1.0"
name: prompt-retry-bp
id: prompt-retry-bp
steps:
  - id: prompt-step
    run: |
      if [ -f pty_flag.txt ]; then
        echo "pty retry success" > pty_retry.txt
        exit 0
      else
        touch pty_flag.txt
        exit 1
      fi
    on_failure: prompt_user
""",
            runner=run_dovo,
        )

        result = run_dovo_pty(
            ["run", "prompt-retry-bp", "--no-worktree"],
            cwd=initialized_project,
            prompt_replies=[("[r/c/a]", "r\n")],
        )

        assert result.exit_code == 0
        assert "retry" in result.transcript.lower()
        retry_file = initialized_project / "pty_retry.txt"
        assert retry_file.is_file()
        assert retry_file.read_text().strip() == "pty retry success"

    def test_prompt_user_interactive_pty_continue_completes_run(
        self, run_dovo_pty: DovoPtyRunner, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "prompt-continue-bp",
            """version: "1.0"
name: prompt-continue-bp
id: prompt-continue-bp
steps:
  - id: failing-step
    run: exit 1
    on_failure: prompt_user
  - id: downstream-step
    run: echo "continued downstream" > continued.txt
""",
            runner=run_dovo,
        )

        result = run_dovo_pty(
            ["run", "prompt-continue-bp", "--no-worktree"],
            cwd=initialized_project,
            prompt_replies=[("[r/c/a]", "c\n")],
        )

        assert result.exit_code == 0
        continued_file = initialized_project / "continued.txt"
        assert continued_file.is_file()
        assert continued_file.read_text().strip() == "continued downstream"

    def test_prompt_user_interactive_pty_abort_terminates_run(
        self, run_dovo_pty: DovoPtyRunner, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "prompt-abort-bp",
            """version: "1.0"
name: prompt-abort-bp
id: prompt-abort-bp
steps:
  - id: failing-step
    run: exit 1
    on_failure: prompt_user
  - id: skipped-step
    run: echo "should not execute" > should_not_exist.txt
""",
            runner=run_dovo,
        )

        result = run_dovo_pty(
            ["run", "prompt-abort-bp", "--no-worktree"],
            cwd=initialized_project,
            prompt_replies=[("[r/c/a]", "a\n")],
        )

        assert result.exit_code == 1
        assert not (initialized_project / "should_not_exist.txt").exists()

    def test_prompt_user_no_tty_aborts_without_stdin(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        write_blueprint(
            initialized_project,
            "prompt-no-tty-bp",
            """version: "1.0"
name: prompt-no-tty-bp
id: prompt-no-tty-bp
steps:
  - id: failing-step
    run: exit 1
    on_failure: prompt_user
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "prompt-no-tty-bp", "--no-worktree", "--no-tty"],
            cwd=initialized_project,
        )

        assert result.exit_code == 1
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "Run Failed" in combined_output or "failed" in combined_output.lower()

    def test_loop_block_executes_until_condition_evaluates_true(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "loop-until-bp",
            """version: "1.0"
name: loop-until-bp
id: loop-until-bp
steps:
  - id: counter-loop
    type: loop
    max_iterations: 5
    until:
      - "iteration >= 2"
    do:
      - id: counter
        run: |
          val=$(cat count.txt 2>/dev/null || echo 0)
          val=$((val + 1))
          echo "$val" > count.txt
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "loop-until-bp", "--no-worktree", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = find_event(result.stdout, "RunSuccessEvent")
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("status") == "completed"

        count_file = initialized_project / "count.txt"
        assert count_file.is_file()
        assert count_file.read_text().strip() == "2"

    def test_loop_block_max_iterations_ceiling_triggers_configured_policy(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        write_blueprint(
            initialized_project,
            "loop-ceiling-bp",
            """version: "1.0"
name: loop-ceiling-bp
id: loop-ceiling-bp
steps:
  - id: ceiling-loop
    type: loop
    max_iterations: 2
    until:
      - "iteration >= 10"
    do:
      - id: noop
        run: echo "iter"
    on_max_iterations: abort
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "loop-ceiling-bp", "--no-worktree"],
            cwd=initialized_project,
        )

        assert result.exit_code == 1
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "reached max_iterations" in combined_output

    def test_step_timeout_terminates_hanging_execution(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        write_blueprint(
            initialized_project,
            "step-timeout-bp",
            """version: "1.0"
name: step-timeout-bp
id: step-timeout-bp
steps:
  - id: hanging-step
    run: sleep 10
    timeout_seconds: 1
    on_failure: abort
""",
            runner=run_dovo,
        )

        result = run_dovo(
            ["run", "step-timeout-bp", "--no-worktree", "--no-tty"],
            cwd=initialized_project,
        )

        assert result.exit_code == 1
        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "timed out after 1 seconds" in combined_output
