"""Integration tests for StepExecution per-attempt stdout/stderr log capture."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import (
    AgentStepRunner,
    ExecutionMetadata,
    OutputCallback,
    StepDispatchOutcome,
    StepExecutionContext,
)
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder


class StepExecutionAttemptLogFileTests:
    """[tier-1/integration] StepExecution: per-attempt log files under session_log_dir."""

    def test_run_process_writes_stdout_lines_to_attempt_log_file_immediately(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: two stdout lines land verbatim in the attempt's .stdout.log; .stderr.log is empty."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        step = StepBuilder.command("echo one; echo two").with_id("build").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, session_log_dir=log_dir)).run()

        assert result.status == "completed"
        assert (log_dir / "01_build_attempt_1.stdout.log").read_text(encoding="utf-8") == "one\ntwo\n"
        assert (log_dir / "01_build_attempt_1.stderr.log").read_text(encoding="utf-8") == ""

    def test_run_process_skips_attempt_log_files_when_save_attempt_logs_false(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: save_attempt_logs=False creates no attempt log files and leaves status unaffected."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        step = StepBuilder.command("echo one").with_id("build").build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, session_log_dir=log_dir, save_attempt_logs=False)
        ).run()

        assert result.status == "completed"
        assert list(log_dir.iterdir()) == []

    def test_run_process_retry_writes_separate_log_file_per_attempt(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: a step retried once keeps attempt 1's and attempt 2's logs side by side."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        marker = tmp_path / "marker"
        command = f'if [ -f "{marker}" ]; then echo second; else touch "{marker}"; echo first; exit 1; fi'
        step = StepBuilder.command(command).with_id("flaky").with_retry(max_retries=2, backoff_ms=0).build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, session_log_dir=log_dir)).run()

        assert result.attempts == 2
        assert sorted(p.name for p in log_dir.iterdir()) == [
            "01_flaky_attempt_1.stderr.log",
            "01_flaky_attempt_1.stdout.log",
            "01_flaky_attempt_2.stderr.log",
            "01_flaky_attempt_2.stdout.log",
        ]
        assert (log_dir / "01_flaky_attempt_1.stdout.log").read_text(encoding="utf-8") == "first\n"
        assert (log_dir / "01_flaky_attempt_2.stdout.log").read_text(encoding="utf-8") == "second\n"

    @pytest.mark.skipif(not Path("/dev/full").exists(), reason="requires /dev/full to simulate a full disk")
    def test_run_process_log_write_failure_does_not_fail_the_step(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: an OSError from the attempt log write becomes a StepResult warning; status reflects only the command."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "01_build_attempt_1.stdout.log").symlink_to("/dev/full")
        step = StepBuilder.command("echo one").with_id("build").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, session_log_dir=log_dir)).run()

        assert (result.status, result.exit_code, result.stdout) == ("completed", 0, "one\n")
        assert len(result.warnings) == 1
        assert "No space left on device" in result.warnings[0]


def _emitting_agent_runner(lines: list[tuple[str, str]], received: list[OutputCallback | None]) -> AgentStepRunner:
    """Return an agent runner that records the line callback it was given and replays lines through it."""

    def _runner(
        step: StepDefinition,
        worktree: Path,
        on_output: OutputCallback | None,
        on_agent_line: OutputCallback | None,
        metadata: ExecutionMetadata,
    ) -> StepDispatchOutcome:
        received.append(on_agent_line)
        if on_agent_line is not None:
            for stream_name, line in lines:
                on_agent_line(stream_name, line)
        return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")

    return _runner


class StepExecutionAgentAttemptLogTests:
    def test_agent_lines_land_in_the_matching_stream_logs_and_never_reach_on_output(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution.run: an agent runner calling on_agent_line for ('stdout','a'), ('stderr','b'), ('stdout','c') leaves 01_<step>_attempt_1.stdout.log == 'a\\nc\\n' and .stderr.log == 'b\\n', and the on_output callback receives none of those lines."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        streamed: list[tuple[str, str]] = []
        runner = _emitting_agent_runner([("stdout", "a"), ("stderr", "b"), ("stdout", "c")], [])
        step = StepBuilder.agent("plan").with_id("plan").build()

        StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                session_log_dir=log_dir,
                agent_runner=runner,
                on_output=lambda stream, line: streamed.append((stream, line)),
            )
        ).run()

        assert (log_dir / "01_plan_attempt_1.stdout.log").read_text(encoding="utf-8") == "a\nc\n"
        assert (log_dir / "01_plan_attempt_1.stderr.log").read_text(encoding="utf-8") == "b\n"
        assert streamed == []

    def test_disabled_capture_passes_no_line_callback_and_creates_no_logs(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution.run: save_attempt_logs=False makes the agent runner receive on_agent_line None, and the log directory stays empty."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        received: list[OutputCallback | None] = []
        step = StepBuilder.agent("plan").with_id("plan").build()

        StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                session_log_dir=log_dir,
                save_attempt_logs=False,
                agent_runner=_emitting_agent_runner([("stdout", "a")], received),
            )
        ).run()

        assert received == [None]
        assert list(log_dir.iterdir()) == []

    @pytest.mark.skipif(not Path("/dev/full").exists(), reason="requires /dev/full to simulate a full disk")
    def test_log_write_failure_adds_one_warning_and_keeps_the_attempt_completed(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution.run: an OSError writing the stdout attempt log gives StepResult.status 'completed' and exactly one warning containing 'No space left on device'."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "01_plan_attempt_1.stdout.log").symlink_to("/dev/full")
        step = StepBuilder.agent("plan").with_id("plan").build()
        runner = _emitting_agent_runner([("stdout", "a"), ("stdout", "b")], [])

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, session_log_dir=log_dir, agent_runner=runner)
        ).run()

        assert result.status == "completed"
        assert len(result.warnings) == 1
        assert "No space left on device" in result.warnings[0]
