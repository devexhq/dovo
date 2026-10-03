"""Contract tests for StepExecution shorthand resolution."""

from __future__ import annotations

from pathlib import Path

from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution


class StepExecutionResolutionTests:
    """[tier-1/unit] StepExecution.run: uses/run shorthand resolution before dispatch."""

    def test_unresolvable_uses_step_returns_failed_result_with_could_not_resolve_message(self, tmp_path: Path) -> None:
        """[tier-1/unit] StepExecution.run: a uses: step with no workspace paths returns status == "failed" and error_message == "Could not resolve step 's1'."."""
        step = StepDefinition(id="s1", uses="base-step")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message == "Could not resolve step 's1'."

    def test_run_shorthand_step_executes_as_command(self, tmp_path: Path) -> None:
        """[tier-1/integration] StepExecution.run: StepDefinition(id="s1", run="echo ok") returns status == "completed" with "ok" in stdout."""
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert "ok" in result.stdout
