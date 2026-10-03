from __future__ import annotations

import pytest

from worktree.core.catalog.definitions import ParsedCondition, parse_condition_expression, validate_condition_expression

PARSER_OPERATOR_CASES = [
    pytest.param(
        "iteration.index == 1",
        ParsedCondition(raw="iteration.index == 1", left="iteration.index", operator="==", right="1"),
        id="eq",
    ),
    pytest.param(
        "iteration.index != 1",
        ParsedCondition(raw="iteration.index != 1", left="iteration.index", operator="!=", right="1"),
        id="ne",
    ),
    pytest.param(
        "iteration.index >= 1",
        ParsedCondition(raw="iteration.index >= 1", left="iteration.index", operator=">=", right="1"),
        id="ge",
    ),
    pytest.param(
        "iteration.index <= 1",
        ParsedCondition(raw="iteration.index <= 1", left="iteration.index", operator="<=", right="1"),
        id="le",
    ),
    pytest.param(
        "iteration.index > 1",
        ParsedCondition(raw="iteration.index > 1", left="iteration.index", operator=">", right="1"),
        id="gt",
    ),
    pytest.param(
        "iteration.index < 1",
        ParsedCondition(raw="iteration.index < 1", left="iteration.index", operator="<", right="1"),
        id="lt",
    ),
    pytest.param(
        "steps.build.stdout contains success",
        ParsedCondition(
            raw="steps.build.stdout contains success",
            left="steps.build.stdout",
            operator="contains",
            right="success",
        ),
        id="contains",
    ),
]


class ConditionExpressionParserTests:
    """Unit tests verifying parser behavior across all supported comparison operators."""

    @pytest.mark.parametrize(("expression", "expected"), PARSER_OPERATOR_CASES)
    def test_parse_condition_expression_supports_all_comparison_operators(
        self,
        expression: str,
        expected: ParsedCondition,
    ) -> None:
        """Verify each registered operator is recognized into a ParsedCondition structure."""
        assert parse_condition_expression(expression) == expected


class ConditionExpressionValidatorTests:
    """Unit tests verifying semantic validation of until condition expressions."""

    def test_validate_condition_expression_detects_unknown_step_ids(self) -> None:
        """Validation returns error diagnostic when condition references step not in known steps."""
        expression = "steps.unknown_step.exit_code == 0"
        errors = validate_condition_expression(expression, known_step_ids={"build"})
        assert errors == [
            "Step id 'unknown_step' referenced in until condition 'steps.unknown_step.exit_code == 0' not found in loop 'do' steps (allowed: build)."
        ]
