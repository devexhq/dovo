from __future__ import annotations

import sys
from pathlib import Path

from dovo.core.catalog.definitions import StepAssert, StepDefinition, StepType
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution


class StepRunnerAssertionContractTests:
    """Contract tests for step runner assertion evaluation and status resolution."""

    def test_passing_assertion_leaves_step_completed(self, tmp_path: Path) -> None:
        (tmp_path / "present.bin").write_text("x", encoding="utf-8")
        step = StepDefinition(
            id="test-assert-pass", type=StepType.COMMAND, command="true", assert_=StepAssert(file_exists="present.bin")
        )

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert (result.status, result.error_message) == ("completed", None)

    def test_assertion_failure_marks_step_failed_preserving_exit_code_zero(self, tmp_path: Path) -> None:
        step = StepDefinition(
            id="test-assert-fail",
            type=StepType.COMMAND,
            command=f"{sys.executable} -c \"print('ok')\"",
            assert_=StepAssert(file_exists="missing.bin"),
        )
        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert (result.status, result.exit_code) == ("failed", 0)
        assert result.error_message == (
            "Step 'test-assert-fail' failed assertion checks:\n  [FAIL] file_exists: path 'missing.bin' does not exist"
        )
