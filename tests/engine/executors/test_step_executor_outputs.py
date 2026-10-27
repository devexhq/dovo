"""Integration tests for StepExecution DOVO_TEMP/DOVO_OUTPUT env var injection and output parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder


class StepExecutionTempEnvVarTests:
    def test_command_step_with_session_tmp_dir_receives_all_four_temp_env_vars_with_expected_values(
        self, tmp_path: Path
    ) -> None:
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        session_tmp_dir = tmp_path / "session"
        step = (
            StepBuilder.command('echo "$DOVO_TEMP|$DOVO_RUNNER_TEMP|$DOVO_STEP_TEMP|$DOVO_OUTPUT"')
            .with_id("s1")
            .build()
        )

        result = StepExecution(
            StepExecutionContext(
                step=step, worktree_path=worktree_path, session_tmp_dir=session_tmp_dir, diagnostics=[]
            )
        ).run()

        assert result.status == "completed"
        assert result.stdout == (
            f"{session_tmp_dir}|{session_tmp_dir}|{session_tmp_dir / 'steps' / 's1'}|"
            f"{session_tmp_dir / 'step_s1.output'}\n"
        )

    def test_command_step_without_session_tmp_dir_omits_all_four_temp_env_vars(self, tmp_path: Path) -> None:
        """Leave all four temp variables unset, not just empty, when session_tmp_dir is None."""
        step = StepBuilder.command('[ -z "${DOVO_TEMP+x}" ]').with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])).run()

        assert result.status == "completed"
        assert result.exit_code == 0


class StepExecutionOutputParsingTests:
    @pytest.mark.parametrize(
        ("command", "outputs", "warnings"),
        [
            pytest.param(
                'echo "greeting=hello" >> "$DOVO_OUTPUT"; echo "count=2" >> "$DOVO_OUTPUT"',
                {"greeting": "hello", "count": "2"},
                [],
                id="key_value_lines",
            ),
            pytest.param(
                'printf "\\n# a comment\\nkey=value\\n" >> "$DOVO_OUTPUT"',
                {"key": "value"},
                [],
                id="blank_and_comment_lines_ignored",
            ),
            pytest.param(
                'printf "malformed_no_equals\\nkey=value\\n" >> "$DOVO_OUTPUT"',
                {"key": "value"},
                ["Malformed step output line, missing '=': 'malformed_no_equals'"],
                id="line_without_equals_warns",
            ),
            pytest.param("echo hi", {}, [], id="missing_output_file"),
            pytest.param(
                'printf "body<<EOF\\nline one\\n\\nline=three\\nEOF\\n" >> "$DOVO_OUTPUT"',
                {"body": "line one\n\nline=three"},
                [],
                id="heredoc_multiline_verbatim",
            ),
            pytest.param(
                'printf "plain=value\\nbody<<EOF\\nline a\\nline b\\nEOF\\n" >> "$DOVO_OUTPUT"',
                {"plain": "value", "body": "line a\nline b"},
                [],
                id="heredoc_alongside_plain_line",
            ),
            pytest.param(
                'printf "body<<EOF\\nline a\\nline b\\n" >> "$DOVO_OUTPUT"',
                {},
                ["Malformed step output heredoc, missing terminator 'EOF' for key 'body'"],
                id="unterminated_heredoc_drops_key_and_warns",
            ),
        ],
    )
    def test_command_step_output_file_is_parsed_into_outputs_and_warnings(
        self, command: str, outputs: dict[str, str], warnings: list[str], tmp_path: Path
    ) -> None:
        step = StepBuilder.command(command).with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(
                step=step, worktree_path=tmp_path, session_tmp_dir=tmp_path / "session", diagnostics=[]
            )
        ).run()

        assert (result.status, result.outputs, result.warnings) == ("completed", outputs, warnings)

    def test_command_step_retry_truncates_output_file_between_attempts(self, tmp_path: Path) -> None:
        step = (
            StepBuilder.command(
                'if [ "$DOVO_STEP_ATTEMPT" -eq 1 ]; then echo "stale=yes" >> "$DOVO_OUTPUT"; exit 1; '
                'else echo "fresh=yes" >> "$DOVO_OUTPUT"; exit 0; fi'
            )
            .with_id("s1")
            .with_retry(max_retries=2, backoff_ms=0)
            .build()
        )

        result = StepExecution(
            StepExecutionContext(
                step=step, worktree_path=tmp_path, session_tmp_dir=tmp_path / "session", diagnostics=[]
            )
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"fresh": "yes"}
