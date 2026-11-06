from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.core.agents import AgentResponseStatus
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.inputs import build_providers, interpolate_string
from dovo.engine.executors import evaluate_condition
from dovo.engine.executors.models import (
    AgentStepSummary,
    ConditionEvaluationResult,
    ExecutionMetadata,
    PreviousStepMetadata,
    StepMetadata,
    StepRecord,
    agent_exit_code,
)
from tests.conftest import PublishArtifact


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
        build = StepRecord(
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
        step_res = StepRecord(
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
        step_res = StepRecord(
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

    @pytest.mark.parametrize(
        ("expression", "resolved", "passed", "detail"),
        [
            pytest.param('steps.gate.outputs.verdict == "needs_edits"', True, True, "TRUE", id="verdict_equals"),
            pytest.param("steps.gate.outputs.count > 2", True, True, "TRUE", id="count_above"),
            pytest.param("steps.gate.outputs.count == 3", True, True, "TRUE", id="count_equals"),
            pytest.param('steps.gate.outputs.missing == "x"', False, False, "FALSE (not found)", id="missing_key"),
            pytest.param("steps.gate.outputs.report.score >= 80", True, True, "TRUE", id="further_path"),
        ],
    )
    def test_dovo_output_values_resolve_in_conditions(
        self, expression: str, resolved: bool, passed: bool, detail: str
    ) -> None:
        result = evaluate_condition(expression, step_results={"gate": _gate_result()})

        assert (result.resolved, result.passed, result.detail) == (resolved, passed, detail)

    def test_placeholder_renders_the_value_the_condition_reads(self) -> None:
        gate = PreviousStepMetadata(
            id="gate", index="1", status="completed", exit_code="0", outputs=_gate_result().outputs
        )
        metadata = ExecutionMetadata(step=StepMetadata(id="next", index=2), steps=[gate])

        rendered = interpolate_string("{{ steps.gate.outputs.report.score }}", build_providers(metadata=metadata))
        result = evaluate_condition("steps.gate.outputs.report.score == 85", step_results={"gate": _gate_result()})

        assert (rendered, result.passed) == ("85", True)

    def test_outputs_without_a_key_is_not_found(self) -> None:
        build = StepRecord(
            step_id="build",
            status="completed",
            exit_code=0,
            stdout=json.dumps({"verdict": "ok"}),
            stderr="",
            duration_seconds=0.1,
        )

        result = evaluate_condition("steps.build.outputs == 'x'", step_results={"build": build})

        assert (result.resolved, result.passed, result.detail) == (False, False, "FALSE (not found)")

    @pytest.mark.parametrize(
        ("expression", "stdout", "outcome"),
        [
            pytest.param("steps.build.stdout", "x", (True, True, "TRUE"), id="non_empty_stdout"),
            pytest.param("steps.build.stdout", "", (False, True, "FALSE (was '')"), id="empty_stdout"),
            pytest.param("steps.other.stdout", "x", (False, False, "FALSE (not found)"), id="step_without_result"),
        ],
    )
    def test_bare_value_entry_passes_when_truthy(
        self, expression: str, stdout: str, outcome: tuple[bool, bool, str]
    ) -> None:
        result = evaluate_condition(expression, step_results={"build": _build_result(stdout=stdout)})

        assert (result.passed, result.resolved, result.detail) == outcome

    @pytest.mark.parametrize(
        ("expression", "outcome"),
        [
            pytest.param(
                'steps.a.status == "completed" || steps.b.exit_code == 0', (True, True, "TRUE"), id="either_holds"
            ),
            pytest.param('!(steps.a.status == "completed")', (True, False, "TRUE"), id="negated_unrun_step"),
            pytest.param(
                'steps.a.outputs.status in ["x", "y"]',
                (False, False, "FALSE (not found)"),
                id="membership_on_unrun_step",
            ),
        ],
    )
    def test_compound_entry_reads_step_results(self, expression: str, outcome: tuple[bool, bool, str]) -> None:
        result = evaluate_condition(expression, step_results={"b": _build_result(step_id="b", exit_code=0)})

        assert (result.passed, result.resolved, result.detail) == outcome

    def test_contains_on_a_recorded_json_array_matches_text_inside_it(self) -> None:
        gate = _gate_result(touched_files='["ba.py"]')

        result = evaluate_condition('steps.gate.outputs.touched_files contains "a.py"', step_results={"gate": gate})

        assert (result.resolved, result.passed) == (True, True)

    @pytest.mark.parametrize(
        ("expression", "outcome"),
        [
            pytest.param(
                "length(fromJSON(steps.gate.outputs.files)) == 2", (True, True, "TRUE"), id="length_of_recorded_list"
            ),
            pytest.param(
                "contains(fromJSON(steps.gate.outputs.files), 'b.py')", (True, True, "TRUE"), id="contains_item"
            ),
            pytest.param("length(steps.gate.exit_code) > 0", (False, False, "FALSE (not found)"), id="wrong_type"),
        ],
    )
    def test_function_reads_step_results(self, expression: str, outcome: tuple[bool, bool, str]) -> None:
        gate = _gate_result(files='["a.py", "b.py"]')

        result = evaluate_condition(expression, step_results={"gate": gate})

        assert (result.resolved, result.passed, result.detail) == outcome

    @pytest.mark.parametrize(
        ("stdout", "outputs"),
        [
            pytest.param('{"reviews": [{"verdict": "approve"}, {"verdict": "edit"}]}', None, id="stdout_list"),
            pytest.param("", {"reviews": '[{"verdict": "approve"}, {"verdict": "edit"}]'}, id="recorded_string"),
        ],
    )
    def test_projection_reads_step_results(self, stdout: str, outputs: dict[str, str] | None) -> None:
        step = _build_result(step_id="a", stdout=stdout, outputs=outputs)

        result = evaluate_condition("contains(steps.a.outputs.reviews.*.verdict, 'edit')", step_results={"a": step})

        assert (result.passed, result.resolved, result.detail) == (True, True, "TRUE")

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("steps.build.exit_code ==", id="dangling_operator"),
            pytest.param("", id="empty"),
        ],
    )
    def test_entry_without_a_comparison_is_a_syntax_error(self, expression: str) -> None:
        result = evaluate_condition(expression, step_results={"build": _build_result()})

        assert result == ConditionEvaluationResult(
            expression=expression,
            passed=False,
            resolved=True,
            actual=None,
            expected=None,
            detail="FALSE (syntax error)",
        )

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("steps.build.outputs['x.y'] == 1", id="bracket_output_key"),
            pytest.param("steps['build'].exit_code == 0", id="bracket_step_id"),
            pytest.param('"a == b" == steps.build.stdout', id="quoted_operator_on_the_left"),
            pytest.param('steps.contains.status == "completed"', id="contains_step_id"),
        ],
    )
    def test_form_load_validation_used_to_reject_evaluates(self, expression: str) -> None:
        step_results = {
            "build": _build_result(stdout="a == b", outputs={"x.y": "1"}),
            "contains": _build_result(step_id="contains"),
        }

        result = evaluate_condition(expression, step_results=step_results)

        assert (result.passed, result.detail) == (True, "TRUE")

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("steps.build.status == completed", id="bare_word"),
            pytest.param("steps.build.stdout == steps", id="lone_steps_word"),
            pytest.param("iteration == 2", id="lone_iteration"),
            pytest.param("iteration.attempt == 2", id="iteration_attempt"),
            pytest.param('steps.build.status.any == "completed"', id="field_with_a_further_segment"),
        ],
    )
    def test_lone_name_and_iteration_operands_resolve(self, expression: str) -> None:
        result = evaluate_condition(
            expression, iteration_index=2, step_results={"build": _build_result(stdout="steps")}
        )

        assert result.passed is True

    @pytest.mark.parametrize(
        ("expression", "passed", "expected", "detail"),
        [
            pytest.param("steps.build.outputs.n == 007", True, 7, "TRUE", id="leading_zero_integer"),
            pytest.param("steps.build.exit_code == (1)", True, 1, "TRUE", id="parenthesised_number"),
        ],
    )
    def test_right_operand_reads_by_the_grammar(
        self, expression: str, passed: bool, expected: object, detail: str
    ) -> None:
        build = _build_result(exit_code=1, outputs={"n": "7"})

        result = evaluate_condition(expression, step_results={"build": build})

        assert (result.passed, result.expected, result.detail) == (passed, expected, detail)

    def test_unknown_iteration_field_is_not_found(self) -> None:
        result = evaluate_condition("iteration.foo == 1", iteration_index=1)

        assert (result.resolved, result.passed, result.detail) == (False, False, "FALSE (not found)")

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param('steps[0].status == "completed"', id="step_by_position"),
            pytest.param('steps.build.outputs.files[0] == "a"', id="index_in_an_output_path"),
        ],
    )
    def test_bracket_index_finds_no_value(self, expression: str) -> None:
        build = _build_result(outputs={"files": '["a"]'})

        result = evaluate_condition(expression, step_results={"build": build})

        assert (result.passed, result.resolved, result.detail) == (False, False, "FALSE (not found)")


def _build_result(
    *, step_id: str = "build", exit_code: int = 0, stdout: str = "", outputs: dict[str, str] | None = None
) -> StepRecord:
    """Build a completed command result with the given exit code, stdout and recorded outputs."""
    return StepRecord(
        step_id=step_id,
        status="completed",
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_seconds=0.1,
        outputs=outputs or {},
    )


def _gate_result(**outputs: str) -> StepRecord:
    """Build a `gate` command result with recorded outputs and plain stdout."""
    recorded = {"verdict": "needs_edits", "count": "3", "report": '{"score": 85}', **outputs}
    return StepRecord(
        step_id="gate",
        status="completed",
        exit_code=0,
        stdout="done\n",
        stderr="",
        duration_seconds=0.1,
        outputs=recorded,
    )


def _agent_result(status: AgentResponseStatus | None = None, error_code: ErrorCode | None = None) -> StepRecord:
    """Build the StepRecord an agent step stores: completed with a status, or failed with an error code."""
    code = agent_exit_code(error_code)
    summary = AgentStepSummary(status=status, summary=None, unfixable_reason=None, touched_files=[])
    return StepRecord(
        step_id="agent",
        status="completed" if error_code is None else "failed",
        exit_code=code,
        stdout=summary.model_dump_json() + "\n",
        stderr="",
        duration_seconds=0.1,
        error_code=error_code,
        outputs=summary.as_outputs(),
    )


_COMPLETED_STATUSES = (AgentResponseStatus.PROPOSED_PATCH, AgentResponseStatus.NO_OP)


class AgentStatusConditionTests:
    @pytest.mark.parametrize("status", _COMPLETED_STATUSES)
    def test_outputs_status_matches_only_its_own_summary(self, status: AgentResponseStatus) -> None:
        step_results = {"agent": _agent_result(status)}

        results = {
            candidate: evaluate_condition(
                f'steps.agent.outputs.status == "{candidate.value}"', step_results=step_results
            )
            for candidate in _COMPLETED_STATUSES
        }

        assert results[status].passed is True
        assert results[status].actual == status.value
        assert {candidate for candidate, result in results.items() if result.passed} == {status}

    def test_failed_attempt_records_an_empty_status(self) -> None:
        result = evaluate_condition(
            'steps.agent.outputs.status == ""',
            step_results={"agent": _agent_result(error_code=ErrorCode.AGENT_TIMEOUT)},
        )

        assert (result.passed, result.actual) == (True, "")

    def test_error_code_condition_names_the_failure(self) -> None:
        outcomes = {
            code: evaluate_condition(
                'steps.agent.error_code == "AGENT_TIMEOUT"', step_results={"agent": _agent_result(error_code=code)}
            ).passed
            for code in (ErrorCode.AGENT_TIMEOUT, ErrorCode.AGENT_TASK_UNFIXABLE, ErrorCode.AGENT_PROVIDER_FAILED)
        }

        assert outcomes == {
            ErrorCode.AGENT_TIMEOUT: True,
            ErrorCode.AGENT_TASK_UNFIXABLE: False,
            ErrorCode.AGENT_PROVIDER_FAILED: False,
        }

    def test_error_code_is_empty_for_a_completed_step(self) -> None:
        result = evaluate_condition(
            'steps.agent.error_code == ""',
            step_results={"agent": _agent_result(AgentResponseStatus.NO_OP)},
        )

        assert (result.passed, result.resolved, result.actual) == (True, True, "")

    def test_error_code_of_a_step_that_has_not_run_is_unresolved(self) -> None:
        result = evaluate_condition('steps.agent.error_code == ""', step_results={})

        assert (result.passed, result.resolved) == (False, False)

    def test_exit_code_condition_distinguishes_timeout(self) -> None:
        outcomes = {
            code: evaluate_condition(
                "steps.agent.exit_code == 202", step_results={"agent": _agent_result(error_code=code)}
            ).passed
            for code in (ErrorCode.AGENT_TIMEOUT, ErrorCode.AGENT_TASK_UNFIXABLE, ErrorCode.AGENT_PROVIDER_FAILED)
        }

        assert outcomes == {
            ErrorCode.AGENT_TIMEOUT: True,
            ErrorCode.AGENT_TASK_UNFIXABLE: False,
            ErrorCode.AGENT_PROVIDER_FAILED: False,
        }


_NOT_FOUND = (False, False, "FALSE (not found)")


class ArtifactConditionTests:
    @pytest.mark.parametrize("field", ["content", "revision", "digest", "dir"])
    def test_condition_reads_each_field_of_the_latest_revision(
        self,
        field: str,
        artifact_store: ArtifactStore,
        artifacts_repository: ArtifactsRepository,
        publish_artifact: PublishArtifact,
    ) -> None:
        publish_artifact("plan", {"PLAN.md": "first"})
        publish_artifact("plan", {"PLAN.md": "second"})
        record = artifacts_repository.get("wf_1", "plan")
        assert record is not None
        expected = {"content": "second", "revision": "2", "digest": record.digest, "dir": str(record.path)}[field]
        operand = "2" if field == "revision" else f"'{expected}'"

        result = evaluate_condition(f"artifacts.plan.{field} == {operand}", artifact_store=artifact_store)

        assert (result.passed, result.resolved, result.actual) == (True, True, expected)

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("artifacts.verdict.content.done == true", id="comparison_on_a_json_field"),
            pytest.param("artifacts.verdict.content.done", id="lone_json_field"),
            pytest.param("length(fromJSON(artifacts.findings.content)) == 2", id="function_on_content"),
            pytest.param("contains(artifacts.findings.content.*.severity, 'high')", id="projection_into_content"),
        ],
    )
    def test_condition_reads_a_field_of_json_content(
        self, expression: str, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("verdict", {"verdict.json": '{"done": true}'})
        publish_artifact("findings", {"findings.json": '[{"severity": "low"}, {"severity": "high"}]'})

        result = evaluate_condition(expression, artifact_store=artifact_store)

        assert (result.passed, result.resolved, result.detail) == (True, True, "TRUE")

    @pytest.mark.parametrize(
        ("expression", "with_store"),
        [
            pytest.param("artifacts.missing.content == 'x'", True, id="not_yet_published"),
            pytest.param("artifacts.pair.content == 'x'", True, id="two_file_content"),
            pytest.param("artifacts.binary.content == 'x'", True, id="content_not_utf8"),
            pytest.param("artifacts.notes.content.done == true", True, id="path_into_content_not_json"),
            pytest.param("artifacts.notes.revision.number == 1", True, id="path_after_revision"),
            pytest.param("artifacts.notes.content == 'plain text'", False, id="no_store"),
        ],
    )
    def test_unavailable_artifact_is_not_found(
        self,
        expression: str,
        with_store: bool,
        artifact_store: ArtifactStore,
        publish_artifact: PublishArtifact,
        tmp_path: Path,
    ) -> None:
        publish_artifact("notes", {"notes.txt": "plain text"})
        publish_artifact("pair", {"a.txt": "a", "b.txt": "b"})
        binary = tmp_path / "binary"
        binary.mkdir()
        (binary / "blob.bin").write_bytes(b"\xff\xfe")
        published = artifact_store.publish(
            binary, name="binary", path_glob="*", retention_days=None, provenance=ArtifactProvenance()
        )
        assert published.ok

        result = evaluate_condition(expression, artifact_store=artifact_store if with_store else None)

        assert (result.passed, result.resolved, result.detail) == _NOT_FOUND

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("join(artifacts.notes.content) == 'x'", id="join_on_text"),
            pytest.param("fromJSON(artifacts.notes.content)", id="from_json_on_text"),
        ],
    )
    def test_function_given_an_artifact_value_of_the_wrong_type_is_not_found(
        self, expression: str, artifact_store: ArtifactStore, publish_artifact: PublishArtifact
    ) -> None:
        publish_artifact("notes", {"notes.txt": "plain text"})

        result = evaluate_condition(expression, artifact_store=artifact_store)

        assert (result.passed, result.resolved, result.detail) == _NOT_FOUND
