"""Integration tests for StepExecution secret masking in results, streamed output, and attempt logs."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder


class StepExecutionRedactionTests:
    def test_run_masks_env_secret_in_result_stream_and_attempt_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] StepExecution.run: command step 'echo $ANTHROPIC_API_KEY' with ANTHROPIC_API_KEY="sk-ant-secret-value-123" returns StepResult.stdout == "[REDACTED:ANTHROPIC_API_KEY]\\n", the on_output callback receives ("stdout","[REDACTED:ANTHROPIC_API_KEY]\\n"), and 01_<step>_attempt_1.stdout.log equals "[REDACTED:ANTHROPIC_API_KEY]\\n"."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value-123")
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        streamed: list[tuple[str, str]] = []
        step = StepBuilder.command("echo $ANTHROPIC_API_KEY").with_id("show").build()

        result = StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                session_log_dir=log_dir,
                on_output=lambda stream, line: streamed.append((stream, line)),
            )
        ).run()

        assert result.stdout == "[REDACTED:ANTHROPIC_API_KEY]\n"
        assert streamed == [("stdout", "[REDACTED:ANTHROPIC_API_KEY]\n")]
        assert (log_dir / "01_show_attempt_1.stdout.log").read_text(encoding="utf-8") == (
            "[REDACTED:ANTHROPIC_API_KEY]\n"
        )

    def test_run_masks_step_env_secret_on_stderr(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution.run: step env {"DEPLOY_TOKEN": "s3cr3t-value"} with command 'echo $DEPLOY_TOKEN >&2' returns StepResult.stderr == "[REDACTED:DEPLOY_TOKEN]\\n"."""
        step = StepBuilder.command("echo $DEPLOY_TOKEN >&2").with_env("DEPLOY_TOKEN", "s3cr3t-value").build()
        streamed: list[tuple[str, str]] = []

        result = StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                on_output=lambda stream, line: streamed.append((stream, line)),
            )
        ).run()

        assert result.stderr == "[REDACTED:DEPLOY_TOKEN]\n"
        assert streamed == [("stderr", "[REDACTED:DEPLOY_TOKEN]\n")]

    def test_run_masks_repository_env_file_secrets(self, tmp_path: Path, engine_paths: WorkspacePaths) -> None:
        """[tier-1/integration] StepExecution.run: paths.root_dir/.env containing DB_PASSWORD=hunter2-long and command 'echo hunter2-long' returns StepResult.stdout == "[REDACTED:DB_PASSWORD]\\n"."""
        (engine_paths.root_dir / ".env").write_text("DB_PASSWORD=hunter2-long\n", encoding="utf-8")
        step = StepBuilder.command("echo hunter2-long").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths)).run()

        assert result.stdout == "[REDACTED:DB_PASSWORD]\n"

    def test_run_masks_secret_quoted_in_assertion_failure_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] StepExecution.run: a step printing "s3cr3t-value" (env X_SECRET) whose output_not_contains "s3cr3t-value" assertion fails returns an error_message with no "s3cr3t-value"."""
        monkeypatch.setenv("X_SECRET", "s3cr3t-value")
        step = StepBuilder.command("echo $X_SECRET").assert_output_not_contains("s3cr3t-value").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert "[REDACTED:X_SECRET]" in result.error_message
        assert "s3cr3t-value" not in result.error_message

    def test_run_evaluates_assertions_against_unmasked_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] StepExecution.run: output_contains "s3cr3t-value" passes (status "completed") while StepResult.stdout holds "[REDACTED:X_SECRET]\\n"."""
        monkeypatch.setenv("X_SECRET", "s3cr3t-value")
        step = StepBuilder.command("echo $X_SECRET").assert_output_contains("s3cr3t-value").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.stdout == "[REDACTED:X_SECRET]\n"
