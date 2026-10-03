from __future__ import annotations

import json

import pytest

from worktree.core.agents import AgentResponseStatus
from worktree.engine.executors import evaluate_condition
from worktree.engine.executors.agent_step import AGENT_OUTCOME_EXIT_CODES
from worktree.engine.executors.models import AgentStepSummary, ConditionEvaluationResult, StepResult

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


def _agent_result(status: AgentResponseStatus) -> StepResult:
    """Build the StepResult an agent step stores for the given status: mapped exit code and the summary line."""
    code = AGENT_OUTCOME_EXIT_CODES[status]
    summary = AgentStepSummary(status=status, summary=None, unfixable_reason=None, touched_files=[])
    return StepResult(
        step_id="agent",
        status="completed" if code == 0 else "failed",
        exit_code=code,
        stdout=summary.model_dump_json() + "\n",
        stderr="",
        duration_seconds=0.1,
    )


class AgentStatusConditionTests:
    @pytest.mark.parametrize("status", list(AgentResponseStatus))
    def test_outputs_status_matches_only_its_own_summary(self, status: AgentResponseStatus) -> None:
        """[tier-1/unit] evaluate_condition: with stdout holding the summary line for <status>, `steps.agent.outputs.status == "<status>"` passes with actual == status.value and passes for no other of the five statuses."""
        step_results = {"agent": _agent_result(status)}

        results = {
            candidate: evaluate_condition(
                f'steps.agent.outputs.status == "{candidate.value}"', step_results=step_results
            )
            for candidate in AgentResponseStatus
        }

        assert results[status].passed is True
        assert results[status].actual == status.value
        assert {candidate for candidate, result in results.items() if result.passed} == {status}

    def test_exit_code_condition_distinguishes_timeout(self) -> None:
        """[tier-1/unit] evaluate_condition: `steps.agent.exit_code == 202` passes for the timeout result (exit 202) and fails for the unfixable (201) and provider_error (203) results."""
        outcomes = {
            status: evaluate_condition(
                "steps.agent.exit_code == 202", step_results={"agent": _agent_result(status)}
            ).passed
            for status in (
                AgentResponseStatus.TIMEOUT,
                AgentResponseStatus.UNFIXABLE,
                AgentResponseStatus.PROVIDER_ERROR,
            )
        }

        assert outcomes == {
            AgentResponseStatus.TIMEOUT: True,
            AgentResponseStatus.UNFIXABLE: False,
            AgentResponseStatus.PROVIDER_ERROR: False,
        }
