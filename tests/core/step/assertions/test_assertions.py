from __future__ import annotations

from pathlib import Path

import pytest

from worktree.core.catalog.definitions import StepAssert
from worktree.core.step.assertions import evaluate_assertions, evaluate_json_match
from worktree.core.step.assertions.filesystem import evaluate_file_exists


class StepAssertionOrderingTests:
    """Unit tests verifying deterministic ordering of aggregated assertion failures."""

    def test_multiple_assertion_failures_preserve_deterministic_order(self, tmp_path: Path) -> None:
        """Multiple failing assertions aggregate failures in deterministic evaluation order."""
        assert_config = StepAssert(
            exit_code=0,
            output_contains="expected-marker",
            file_exists="absent.txt",
        )

        result = evaluate_assertions(
            assert_config,
            exit_code=2,
            stdout="actual-output",
            stderr="",
            sandbox_path=tmp_path,
        )

        assert result.passed is False
        assert result.failed_conditions == [
            "exit_code: expected [0], got 2",
            "output_contains: substring 'expected-marker' not found in output",
            "file_exists: path 'absent.txt' does not exist",
        ]
        assert result.message == (
            "exit_code: expected [0], got 2\n"
            "output_contains: substring 'expected-marker' not found in output\n"
            "file_exists: path 'absent.txt' does not exist"
        )
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []


class FilesystemAssertionSafetyTests:
    """Unit tests verifying sandbox boundary enforcement for filesystem assertions."""

    def test_file_exists_assertion_rejects_sandbox_root_escape(self, tmp_path: Path) -> None:
        """File existence assertion attempting traversal outside sandbox root reports escape failure."""
        sandbox_root = tmp_path / "sandbox"
        sandbox_root.mkdir()

        result = evaluate_file_exists("../../../etc/passwd", sandbox_root)

        assert result == ["file_exists: path '../../../etc/passwd' escapes the root path"]


class JsonMatchOrderingInvariantsTests:
    """Unit tests verifying ordering operators reject boolean types without numeric coercion."""

    @pytest.mark.parametrize(
        ("operator", "stdout", "expected_val", "expected_failures"),
        [
            pytest.param(
                "gt",
                '{"flag": true}',
                0,
                ["json_match: operator 'gt' requires numeric values, got bool and int"],
                id="gt_bool_actual",
            ),
            pytest.param(
                "lt",
                '{"flag": 1}',
                False,
                ["json_match: operator 'lt' requires numeric values, got int and bool"],
                id="lt_bool_value",
            ),
            pytest.param(
                "gte",
                '{"flag": true}',
                True,
                ["json_match: operator 'gte' requires numeric values, got bool and bool"],
                id="gte_bool_both",
            ),
            pytest.param(
                "lte",
                '{"flag": false}',
                0,
                ["json_match: operator 'lte' requires numeric values, got bool and int"],
                id="lte_bool_actual",
            ),
        ],
    )
    def test_json_match_ordering_operators_reject_boolean_conversion(
        self,
        operator: str,
        stdout: str,
        expected_val: object,
        expected_failures: list[str],
    ) -> None:
        """Numeric ordering operators reject boolean values to prevent True == 1 coercion bugs."""
        result = evaluate_json_match(
            {"path": "flag", "operator": operator, "value": expected_val},
            stdout,
        )

        assert result == expected_failures
