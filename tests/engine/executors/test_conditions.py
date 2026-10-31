from __future__ import annotations

import json

import pytest

from dovo.core.agents import AgentResponseStatus
from dovo.engine.executors import evaluate_condition
from dovo.engine.executors.models import (
    AGENT_OUTCOME_EXIT_CODES,
    AgentStepSummary,
    StepResult,
)


class ConditionExpressionEvaluatorTests:
    """Unit tests verifying operand resolution and boolean evaluation contracts."""

    @pytest.mark.parametrize(
        ("expression", "resolved", "passed"),
        [
            pytest.param("steps.build.outputs.verdict == 'ok'", True, True, id="present_output"),
            pytest.param("steps.build.outputs.verdict == 'bad'", True, False, id="resolved_false"),
            pytest.param("steps.build.outputs.reason == 'x'", True, False, id="json_null_output_is_resolved"),
            pytest.param("steps.build.outputs.missing == 'x'", False, False, id="missing_output_key"),
            pytest.param("steps.build.outputs.verdict.deeper == 'x'", False, False, id="missing_subpath"),
            pytest.param("steps.other.exit_code == 0", False, False, id="step_without_result"),
            pytest.param("iteration.index >= 1", True, True, id="iteration_operand"),
        ],
    )
    def test_evaluate_condition_reports_whether_its_step_operands_resolved(
        self, expression: str, resolved: bool, passed: bool
    ) -> None:
        build = StepResult(
            step_id="build",
            status="completed",
            exit_code=0,
            stdout=json.dumps({"verdict": "ok", "reason": None}),
            stderr="",
            duration_seconds=0.1,
        )

        result = evaluate_condition(expression, step_results={"build": build})

        assert (result.resolved, result.passed) == (resolved, passed)

    def test_evaluate_condition_resolves_nested_json_subpath_in_step_outputs(self) -> None:
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

    @pytest.mark.parametrize(
        ("literal", "passed", "detail"),
        [
            pytest.param("TRUE", True, "TRUE", id="true_upper"),
            pytest.param("true", True, "TRUE", id="true_lower"),
            pytest.param("True", True, "TRUE", id="true_title"),
            pytest.param("FALSE", False, "FALSE (was True)", id="false_upper"),
            pytest.param("false", False, "FALSE (was True)", id="false_lower"),
            pytest.param("False", False, "FALSE (was True)", id="false_title"),
        ],
    )
    def test_evaluate_condition_parses_boolean_literals_case_insensitively(
        self, literal: str, passed: bool, detail: str
    ) -> None:
        step_res = StepResult(
            step_id="check",
            status="completed",
            exit_code=0,
            stdout=json.dumps({"ready": True}),
            stderr="",
            duration_seconds=0.1,
        )
        result = evaluate_condition(
            f"steps.check.outputs.ready == {literal}",
            step_results={"check": step_res},
        )
        assert (result.passed, result.actual, result.expected, result.detail) == (passed, True, passed, detail)


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
