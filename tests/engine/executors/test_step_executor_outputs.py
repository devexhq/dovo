"""Integration tests for StepExecution WT_TEMP/WT_OUTPUT env var injection and output parsing."""

from __future__ import annotations

from pathlib import Path

from tests.harness.builders import StepBuilder
from worktree.engine.executors.models import StepExecutionContext
from worktree.engine.executors.step_executor import StepExecution


class StepExecutionTempEnvVarTests:
    """[tier-1/integration] StepExecution: WT_TEMP/WT_RUNNER_TEMP/WT_STEP_TEMP/WT_OUTPUT injection into the real subprocess environment."""

    def test_command_step_with_session_tmp_dir_receives_all_four_temp_env_vars_with_expected_values(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] StepExecution: a command echoing $WT_TEMP, $WT_RUNNER_TEMP, $WT_STEP_TEMP, $WT_OUTPUT prints the exact session_tmp_dir, session_tmp_dir, session_tmp_dir/steps/<step_id>, and session_tmp_dir/step_<step_id>.output paths."""
        sandbox_path = tmp_path / "sandbox"
        sandbox_path.mkdir()
        session_tmp_dir = tmp_path / "session"
        step = StepBuilder.command('echo "$WT_TEMP|$WT_RUNNER_TEMP|$WT_STEP_TEMP|$WT_OUTPUT"').with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=sandbox_path, session_tmp_dir=session_tmp_dir)
        ).run()

        assert result.status == "completed"
        assert result.stdout == (
            f"{session_tmp_dir}|{session_tmp_dir}|{session_tmp_dir / 'steps' / 's1'}|"
            f"{session_tmp_dir / 'step_s1.output'}\n"
        )

    def test_command_step_without_session_tmp_dir_omits_all_four_temp_env_vars(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: with StepExecutionContext.session_tmp_dir left as the default None, a command checking `[ -z "${WT_TEMP+x}" ]` (variable unset, not just empty) exits 0."""
        step = StepBuilder.command('[ -z "${WT_TEMP+x}" ]').with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, sandbox_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.exit_code == 0


class StepExecutionOutputParsingTests:
    """[tier-1/integration] StepExecution: $WT_OUTPUT truncation, KEY=VALUE parsing, and StepResult.outputs/warnings population."""

    def test_command_step_writes_key_value_lines_populates_step_result_outputs(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: a command appending 'greeting=hello' and 'count=2' to $WT_OUTPUT yields StepResult.outputs == {'greeting': 'hello', 'count': '2'}."""
        step = (
            StepBuilder.command('echo "greeting=hello" >> "$WT_OUTPUT"; echo "count=2" >> "$WT_OUTPUT"')
            .with_id("s1")
            .build()
        )

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"greeting": "hello", "count": "2"}

    def test_command_step_output_file_blank_and_comment_lines_are_ignored(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: $WT_OUTPUT containing a blank line and a '# comment' line alongside 'key=value' yields StepResult.outputs == {'key': 'value'} with no warnings."""
        step = (
            StepBuilder.command(
                'printf "\\n# a comment\\nkey=value\\n" >> "$WT_OUTPUT"',
            )
            .with_id("s1")
            .build()
        )

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"key": "value"}
        assert result.warnings == []

    def test_command_step_output_file_line_without_equals_recorded_as_warning_not_error(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: $WT_OUTPUT containing 'malformed_no_equals' alongside 'key=value' yields StepResult.status == 'completed', outputs == {'key': 'value'}, and one warning mentioning 'malformed_no_equals'."""
        step = StepBuilder.command('printf "malformed_no_equals\\nkey=value\\n" >> "$WT_OUTPUT"').with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"key": "value"}
        assert len(result.warnings) == 1
        assert "malformed_no_equals" in result.warnings[0]

    def test_command_step_missing_output_file_returns_empty_outputs_without_error(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: a command that never writes to $WT_OUTPUT yields StepResult.status == 'completed', outputs == {}, warnings == []."""
        step = StepBuilder.command("echo hi").with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {}
        assert result.warnings == []

    def test_command_step_retry_truncates_output_file_between_attempts(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: attempt 1 writes 'stale=yes' and exits 1, attempt 2 writes 'fresh=yes' and exits 0; final StepResult.outputs == {'fresh': 'yes'} with no 'stale' key."""
        step = (
            StepBuilder.command(
                'if [ "$WT_STEP_ATTEMPT" -eq 1 ]; then echo "stale=yes" >> "$WT_OUTPUT"; exit 1; '
                'else echo "fresh=yes" >> "$WT_OUTPUT"; exit 0; fi'
            )
            .with_id("s1")
            .with_retry(max_retries=2, backoff_ms=0)
            .build()
        )

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"fresh": "yes"}

    def test_command_step_heredoc_output_captures_multiline_value_verbatim(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: a command writing 'body<<EOF', 'line one', '', 'line=three', 'EOF' to $WT_OUTPUT yields StepResult.outputs == {'body': 'line one\\n\\nline=three'}, with no warnings."""
        step = (
            StepBuilder.command(
                'printf "body<<EOF\\nline one\\n\\nline=three\\nEOF\\n" >> "$WT_OUTPUT"',
            )
            .with_id("s1")
            .build()
        )

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"body": "line one\n\nline=three"}
        assert result.warnings == []

    def test_command_step_heredoc_output_alongside_plain_key_value_line_parses_both(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: $WT_OUTPUT containing 'plain=value' before a 'body<<EOF' ... 'EOF' block yields StepResult.outputs == {'plain': 'value', 'body': <joined lines>}."""
        step = (
            StepBuilder.command(
                'printf "plain=value\\nbody<<EOF\\nline a\\nline b\\nEOF\\n" >> "$WT_OUTPUT"',
            )
            .with_id("s1")
            .build()
        )

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {"plain": "value", "body": "line a\nline b"}

    def test_command_step_unterminated_heredoc_drops_key_and_warns_without_failing_step(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution: $WT_OUTPUT containing 'body<<EOF' followed by lines but no terminating 'EOF' line yields StepResult.status == 'completed', outputs == {}, and one warning mentioning 'body' and the missing terminator."""
        step = (
            StepBuilder.command(
                'printf "body<<EOF\\nline a\\nline b\\n" >> "$WT_OUTPUT"',
            )
            .with_id("s1")
            .build()
        )

        result = StepExecution(
            StepExecutionContext(step=step, sandbox_path=tmp_path, session_tmp_dir=tmp_path / "session")
        ).run()

        assert result.status == "completed"
        assert result.outputs == {}
        assert len(result.warnings) == 1
        assert "body" in result.warnings[0]
        assert "EOF" in result.warnings[0]
