from __future__ import annotations

import sys
from pathlib import Path

from dovo.core.catalog.definitions import StepAssert, StepDefinition, StepType
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution


class StepRunnerAssertionContractTests:
    """Contract tests for step runner assertion evaluation and status resolution."""

    def test_assertion_failure_marks_step_failed_preserving_exit_code_zero(self, tmp_path: Path) -> None:
        """A step whose process exits 0 but whose assertion fails reports failed status with exit_code 0."""
        step = StepDefinition(
            id="test-assert-fail",
            type=StepType.COMMAND,
            command=f"{sys.executable} -c \"print('ok')\"",
            assert_=StepAssert(file_exists="missing.bin"),
        )
        result = StepExecution(StepExecutionContext(step=step, sandbox_path=tmp_path)).run()

        assert result.step_id == "test-assert-fail"
        assert result.status == "failed"
        assert result.exit_code == 0
        assert result.stdout == "ok\n"
        assert result.stderr == ""
        assert result.attempts == 1
        assert result.error_message == (
            "Step 'test-assert-fail' failed assertion checks:\n  [FAIL] file_exists: path 'missing.bin' does not exist"
        )
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert result.duration_seconds >= 0.0
