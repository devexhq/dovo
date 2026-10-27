"""Integration tests for StepExecution secret masking in results, streamed output, and attempt logs."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import (
    AgentProgressSink,
    ExecutionMetadata,
    OutputCallback,
    StepDispatchOutcome,
    StepExecutionContext,
)
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder


class StepExecutionRedactionTests:
    @pytest.mark.parametrize(
        ("name", "value", "listed", "expected"),
        [
            pytest.param(
                "ANTHROPIC_API_KEY",
                "sk-ant-secret-value-123",
                (),
                "[REDACTED:ANTHROPIC_API_KEY]\n",
                id="env_secret",
            ),
            pytest.param("PIN", "1234", ("PIN",), "[REDACTED:PIN]\n", id="listed_short_value"),
            pytest.param("PIN", "1234", (), "1234\n", id="unlisted_short_value"),
        ],
    )
    def test_run_masks_env_secret_in_result_stream_and_attempt_log(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
        listed: tuple[str, ...],
        expected: str,
    ) -> None:
        monkeypatch.setenv(name, value)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        streamed: list[tuple[str, str]] = []
        step = StepBuilder.command(f"echo ${name}").with_id("show").build()

        result = StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                session_log_dir=log_dir,
                on_output=lambda stream, line: streamed.append((stream, line)),
                sensitive_variables=listed,
                diagnostics=[],
            )
        ).run()

        assert result.stdout == expected
        assert streamed == [("stdout", expected)]
        assert (log_dir / "01_show_attempt_1.stdout.log").read_text(encoding="utf-8") == expected

    def test_run_masks_env_secret_in_agent_attempt_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value-123")
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        def _runner(
            step: StepDefinition,
            worktree: Path,
            on_output: OutputCallback | None,
            on_agent_line: OutputCallback | None,
            progress: AgentProgressSink,
            metadata: ExecutionMetadata,
        ) -> StepDispatchOutcome:
            assert on_agent_line is not None
            on_agent_line("stdout", "token sk-ant-secret-value-123")
            return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")

        step = StepBuilder.agent("plan").with_id("plan").build()

        StepExecution(
            StepExecutionContext(
                step=step, worktree_path=tmp_path, session_log_dir=log_dir, agent_runner=_runner, diagnostics=[]
            )
        ).run()

        logged = (log_dir / "01_plan_attempt_1.stdout.log").read_text(encoding="utf-8")
        assert logged == "token [REDACTED:ANTHROPIC_API_KEY]\n"
        assert "sk-ant-secret-value-123" not in logged

    @pytest.mark.parametrize(
        ("name", "value", "listed"),
        [
            pytest.param("DEPLOY_TOKEN", "s3cr3t-value", (), id="secret_pattern"),
            pytest.param("DB_PIN", "99", ("DB_PIN",), id="listed_name"),
        ],
    )
    def test_run_masks_step_env_secret_on_stderr(
        self, tmp_path: Path, name: str, value: str, listed: tuple[str, ...]
    ) -> None:
        step = StepBuilder.command(f"echo ${name} >&2").with_env(name, value).build()
        streamed: list[tuple[str, str]] = []

        result = StepExecution(
            StepExecutionContext(
                step=step,
                worktree_path=tmp_path,
                on_output=lambda stream, line: streamed.append((stream, line)),
                sensitive_variables=listed,
                diagnostics=[],
            )
        ).run()

        assert result.stderr == f"[REDACTED:{name}]\n"
        assert streamed == [("stderr", f"[REDACTED:{name}]\n")]

    def test_run_masks_repository_env_file_secrets(self, tmp_path: Path, engine_paths: WorkspacePaths) -> None:
        (engine_paths.root_dir / ".env").write_text("DB_PASSWORD=hunter2-long\n", encoding="utf-8")
        step = StepBuilder.command("echo hunter2-long").build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, paths=engine_paths, diagnostics=[])
        ).run()

        assert result.stdout == "[REDACTED:DB_PASSWORD]\n"

    @pytest.mark.parametrize(
        ("name", "value", "listed"),
        [
            pytest.param("X_SECRET", "s3cr3t-value", (), id="secret_pattern"),
            pytest.param("PIN", "1234", ("PIN", "UNSET_NAME"), id="listed_name_with_unset_name"),
        ],
    )
    def test_run_masks_secret_quoted_in_assertion_failure_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str, listed: tuple[str, ...]
    ) -> None:
        monkeypatch.setenv(name, value)
        monkeypatch.delenv("UNSET_NAME", raising=False)
        step = StepBuilder.command(f"echo ${name}").assert_output_not_contains(value).build()
        execution = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, sensitive_variables=listed, diagnostics=[])
        )

        result = execution.run()

        assert result.status == "failed"
        assert result.error_message is not None
        assert f"[REDACTED:{name}]" in result.error_message
        assert value not in result.error_message
        assert execution.log_warnings == []

    @pytest.mark.parametrize(
        ("name", "value", "listed"),
        [
            pytest.param("X_SECRET", "s3cr3t-value", (), id="secret_pattern"),
            pytest.param("PIN", "1234", ("PIN",), id="listed_name"),
        ],
    )
    def test_run_evaluates_assertions_against_unmasked_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str, listed: tuple[str, ...]
    ) -> None:
        monkeypatch.setenv(name, value)
        step = StepBuilder.command(f"echo ${name}").assert_output_contains(value).build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, sensitive_variables=listed, diagnostics=[])
        ).run()

        assert result.status == "completed"
        assert result.stdout == f"[REDACTED:{name}]\n"
