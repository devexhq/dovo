"""Contract tests for StepExecution shorthand resolution, context fields and durations."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder


class StepExecutionResolutionTests:
    def test_unresolvable_uses_step_returns_failed_result_with_could_not_resolve_message(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", uses="base-step")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message == "Could not resolve step 's1'."

    def test_run_shorthand_step_executes_as_command(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert "ok" in result.stdout


class CommandScriptEnvironmentPreservedTests:
    @pytest.mark.parametrize("step_type", [pytest.param("command", id="command"), pytest.param("script", id="script")])
    def test_command_and_script_children_still_inherit_ambient_and_keep_precedence(
        self, step_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNRELATED", "1")
        monkeypatch.setenv("DOVO_STEP_ID", "ambient-id")
        monkeypatch.setenv("DOVO_STEP_NAME", "ambient-name")
        if step_type == "command":
            builder = StepBuilder.command('echo "$UNRELATED|$DOVO_STEP_ID|$DOVO_STEP_NAME"')
        else:
            (tmp_path / "probe.py").write_text(
                "import os\nprint('|'.join(os.environ[k] for k in ('UNRELATED', 'DOVO_STEP_ID', 'DOVO_STEP_NAME')))\n",
                encoding="utf-8",
            )
            builder = StepBuilder.script("probe.py")
        step = builder.with_id("probe").with_env("DOVO_STEP_NAME", "from-step").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.stdout.strip() == "1|probe|from-step"


class StepExecutionContextFieldsTests:
    def test_inputs_interpolate_into_command(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo ${{ inputs.feature }}").with_id("s1").build()

        result = StepExecution(
            StepExecutionContext(step=step, worktree_path=tmp_path, inputs={"feature": "export"})
        ).run()

        assert result.stdout == "export\n"

    def test_missing_input_stays_literal_like_a_run_without_inputs(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo '${{ inputs.x }}'").with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.stdout == "${{ inputs.x }}\n"

    def test_iteration_index_follows_loop_iteration(self, tmp_path: Path) -> None:
        step = StepBuilder.command("echo $DOVO_ITERATION_INDEX ${{ iteration.index }}").with_id("s1").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path, loop_iteration=3)).run()

        assert result.stdout == "3 3\n"


def _unread_clock() -> float:
    raise AssertionError("a step failing before dispatch must not read the clock")


class StepExecutionDurationTests:
    def test_completed_step_duration_is_the_attempts_stopwatch_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return a completed StepResult for `run: echo ok` with duration_seconds == 2.5 for readings [10.0, 12.5]."""
        readings = iter([10.0, 12.5])
        monkeypatch.setattr("dovo.common.timing.monotonic", lambda: next(readings))
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.duration_seconds == 2.5

    @pytest.mark.parametrize("failure", ["missing_worktree", "unresolved_step"])
    def test_step_failing_before_dispatch_has_zero_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        """Return a failed StepResult with duration_seconds == 0.0 and read no clock (patched monotonic raises)."""
        monkeypatch.setattr("dovo.common.timing.monotonic", _unread_clock)
        if failure == "missing_worktree":
            context = StepExecutionContext(
                step=StepDefinition(id="s1", run="echo ok"), worktree_path=tmp_path / "missing"
            )
        else:
            context = StepExecutionContext(step=StepDefinition(id="s1", uses="base-step"), worktree_path=tmp_path)

        result = StepExecution(context).run()

        assert result.status == "failed"
        assert result.duration_seconds == 0.0
