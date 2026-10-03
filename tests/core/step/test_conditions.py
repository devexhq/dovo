from __future__ import annotations

import json

import pytest

from worktree.core.step.models import ConditionEvaluationResult, StepResult
from worktree.core.step.services.conditions import evaluate_condition

BOOLEAN_CASING_CASES = [
    pytest.param(
        "steps.check.outputs.ready == TRUE",
        ConditionEvaluationResult(
            expression="steps.check.outputs.ready == TRUE",
            passed=True,
            actual=True,
            expected=True,
            detail="TRUE",
            errors=[],
            warnings=[],
            fixes=[],
        ),
        id="true_upper",
    ),
    pytest.param(
        "steps.check.outputs.ready == true",
        ConditionEvaluationResult(
            expression="steps.check.outputs.ready == true",
            passed=True,
            actual=True,
            expected=True,
            detail="TRUE",
            errors=[],
            warnings=[],
            fixes=[],
        ),
        id="true_lower",
    ),
    pytest.param(
        "steps.check.outputs.ready == True",
        ConditionEvaluationResult(
            expression="steps.check.outputs.ready == True",
            passed=True,
            actual=True,
            expected=True,
            detail="TRUE",
            errors=[],
            warnings=[],
            fixes=[],
        ),
        id="true_title",
    ),
    pytest.param(
        "steps.check.outputs.ready == FALSE",
        ConditionEvaluationResult(
            expression="steps.check.outputs.ready == FALSE",
            passed=False,
            actual=True,
            expected=False,
            detail="FALSE (was True)",
            errors=[],
            warnings=[],
            fixes=[],
        ),
        id="false_upper",
    ),
    pytest.param(
        "steps.check.outputs.ready == false",
        ConditionEvaluationResult(
            expression="steps.check.outputs.ready == false",
            passed=False,
            actual=True,
            expected=False,
            detail="FALSE (was True)",
            errors=[],
            warnings=[],
            fixes=[],
        ),
        id="false_lower",
    ),
    pytest.param(
        "steps.check.outputs.ready == False",
        ConditionEvaluationResult(
            expression="steps.check.outputs.ready == False",
            passed=False,
            actual=True,
            expected=False,
            detail="FALSE (was True)",
            errors=[],
            warnings=[],
            fixes=[],
        ),
        id="false_title",
    ),
]


class ConditionExpressionEvaluatorTests:
    """Unit tests verifying operand resolution and boolean evaluation contracts."""

    def test_evaluate_condition_resolves_nested_json_subpath_in_step_outputs(self) -> None:
        """Verify nested JSON subpath in prior step outputs resolves and evaluates correctly."""
        step_res = StepResult(
            step_id="build",
            status="completed",
            exit_code=0,
            stdout=json.dumps({"metrics": {"coverage": 85}}),
            stderr="",
            duration_seconds=0.1,
        )
        result = evaluate_condition(
            "steps.build.outputs.metrics.coverage >= 80",
            step_results={"build": step_res},
        )
        assert result.expression == "steps.build.outputs.metrics.coverage >= 80"
        assert result.passed is True
        assert result.actual == 85
        assert result.expected == 80
        assert result.detail == "TRUE"
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

    @pytest.mark.parametrize(("expression", "expected"), BOOLEAN_CASING_CASES)
    def test_evaluate_condition_parses_boolean_literals_case_insensitively(
        self,
        expression: str,
        expected: ConditionEvaluationResult,
    ) -> None:
        """Verify TRUE, True, true, FALSE, False, false literals evaluate case-insensitively."""
        step_res = StepResult(
            step_id="check",
            status="completed",
            exit_code=0,
            stdout=json.dumps({"ready": True}),
            stderr="",
            duration_seconds=0.1,
        )
        result = evaluate_condition(
            expression,
            step_results={"check": step_res},
        )
        assert result == expected
