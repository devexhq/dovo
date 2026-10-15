"""Contract tests for engine/executors/assertions/aggregate.py: evaluate_assertions."""

from __future__ import annotations

from pathlib import Path

from dovo.core.catalog.definitions import StepAssert
from dovo.engine.executors.assertions import evaluate_assertions


class EvaluateAssertionsTests:
    def test_default_config_passes_on_zero_exit_code(self, tmp_path: Path) -> None:
        result = evaluate_assertions(StepAssert(), exit_code=0, stdout="", stderr="", worktree_path=tmp_path)

        assert result.passed is True
        assert result.failed_conditions == []
        assert result.message == ""

    def test_default_config_fails_on_nonzero_exit_code(self, tmp_path: Path) -> None:
        result = evaluate_assertions(StepAssert(), exit_code=3, stdout="", stderr="", worktree_path=tmp_path)

        assert result.passed is False
        assert result.failed_conditions == ["exit_code: expected [0], got 3"]

    def test_output_checks_see_stdout_and_stderr_combined(self, tmp_path: Path) -> None:
        config = StepAssert(output_contains="from-stderr")

        result = evaluate_assertions(config, exit_code=0, stdout="out", stderr="from-stderr", worktree_path=tmp_path)

        assert result.passed is True

    def test_json_match_reads_stdout_only(self, tmp_path: Path) -> None:
        config = StepAssert(json_match={"path": "ok", "operator": "eq", "value": True})

        result = evaluate_assertions(config, exit_code=0, stdout="", stderr='{"ok": true}', worktree_path=tmp_path)

        assert result.failed_conditions == ["json_match: Invalid JSON output"]

    def test_filesystem_checks_resolve_under_the_worktree_path(self, tmp_path: Path) -> None:
        (tmp_path / "out.txt").write_text("data", encoding="utf-8")
        config = StepAssert(file_exists="out.txt", file_not_exists="gone.txt", file_not_empty="out.txt")

        result = evaluate_assertions(config, exit_code=0, stdout="", stderr="", worktree_path=tmp_path)

        assert result.passed is True

    def test_multiple_assertion_failures_preserve_deterministic_order(self, tmp_path: Path) -> None:
        config = StepAssert(exit_code=0, output_contains="expected-marker", file_exists="absent.txt")

        result = evaluate_assertions(config, exit_code=2, stdout="actual-output", stderr="", worktree_path=tmp_path)

        assert result.passed is False
        assert result.failed_conditions == [
            "exit_code: expected [0], got 2",
            "output_contains: substring 'expected-marker' not found in output",
            "file_exists: path 'absent.txt' does not exist",
        ]
        assert result.message == "\n".join(result.failed_conditions)
