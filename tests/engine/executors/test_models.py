"""Contract tests for step execution models."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.core.agents import AgentResponseStatus
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.engine.executors.models import (
    AgentProgressSink,
    AgentStepSummary,
    ExecutionIdentity,
    ExecutionMetadata,
    OutputCallback,
    StepDispatchOutcome,
    StepExecutionContext,
    StepRecord,
    agent_exit_code,
)
from tests.harness.builders import StepBuilder


def _runner(
    step: StepDefinition,
    worktree: Path,
    on_output: OutputCallback | None,
    on_agent_line: OutputCallback | None,
    progress: AgentProgressSink,
    metadata: ExecutionMetadata,
) -> StepDispatchOutcome:
    return StepDispatchOutcome(status="completed", exit_code=0, stdout="", stderr="")


def _non_agent_step(kind: str) -> StepDefinition:
    if kind == "command":
        return StepBuilder.command("echo ok").build()
    if kind == "script":
        return StepBuilder.script("run.sh").build()
    if kind == "internal":
        return StepDefinition(id="s1", type=StepType.INTERNAL, command="artifacts.upload")
    return StepDefinition.model_validate(
        {"id": "approve", "type": "ask", "interaction": {"mode": "confirm", "message": "Go?"}}
    )


class AgentStepSummaryTests:
    def test_defaults_and_field_order_serialize_to_exact_json(self) -> None:
        summary = AgentStepSummary(status=AgentResponseStatus.NO_OP)

        assert (
            summary.model_dump_json() == '{"status":"no_op","summary":null,"unfixable_reason":null,"touched_files":[]}'
        )

    def test_unknown_field_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            AgentStepSummary.model_validate({"status": "no_op", "extra": 1})

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            pytest.param(AgentResponseStatus.NO_OP, True, id="no_op"),
            pytest.param(AgentResponseStatus.PROPOSED_PATCH, True, id="proposed_patch"),
            pytest.param(None, False, id="failed"),
        ],
    )
    def test_succeeded_is_true_only_when_a_status_is_named(
        self, status: AgentResponseStatus | None, expected: bool
    ) -> None:
        assert AgentStepSummary(status=status).succeeded is expected

    @pytest.mark.parametrize(
        ("summary", "expected"),
        [
            pytest.param(
                AgentStepSummary(status=AgentResponseStatus.NO_OP),
                {"status": "no_op", "summary": "", "unfixable_reason": "", "touched_files": "[]"},
                id="no_op_with_nulls",
            ),
            pytest.param(
                AgentStepSummary(status=None, unfixable_reason="no access"),
                {"status": "", "summary": "", "unfixable_reason": "no access", "touched_files": "[]"},
                id="failed_with_reason",
            ),
            pytest.param(
                AgentStepSummary(
                    status=AgentResponseStatus.PROPOSED_PATCH, summary="Edited", touched_files=["a.py", "b.py"]
                ),
                {
                    "status": "proposed_patch",
                    "summary": "Edited",
                    "unfixable_reason": "",
                    "touched_files": '["a.py", "b.py"]',
                },
                id="touched_files",
            ),
        ],
    )
    def test_as_outputs(self, summary: AgentStepSummary, expected: dict[str, str]) -> None:
        assert summary.as_outputs() == expected

    def test_failed_summary_serializes_a_null_status(self) -> None:
        assert (
            AgentStepSummary(status=None).model_dump_json()
            == '{"status":null,"summary":null,"unfixable_reason":null,"touched_files":[]}'
        )


class AgentExitCodeTests:
    @pytest.mark.parametrize(
        ("error_code", "expected"),
        [
            pytest.param(None, 0, id="none"),
            pytest.param(ErrorCode.AGENT_TASK_UNFIXABLE, 201, id="unfixable"),
            pytest.param(ErrorCode.AGENT_TIMEOUT, 202, id="timeout"),
            pytest.param(ErrorCode.AGENT_TASK_BLOCKED, 204, id="blocked"),
            pytest.param(ErrorCode.AGENT_PROVIDER_FAILED, 203, id="provider_failed"),
            pytest.param(ErrorCode.STEP_WORKTREE_MISSING, 203, id="worktree_missing"),
            pytest.param(ErrorCode.GIT_FAILED, 203, id="git_failed"),
            pytest.param(ErrorCode.INTERNAL_ERROR, 203, id="internal_error"),
        ],
    )
    def test_exit_code_derives_from_error_code(self, error_code: ErrorCode | None, expected: int) -> None:
        assert agent_exit_code(error_code) == expected


class StepExecutionContextTests:
    def test_agent_step_without_runner_raises_naming_step_id(self, tmp_path: Path) -> None:
        step = StepBuilder.agent("plan").with_id("plan").build()

        with pytest.raises(ValueError, match=r"Step 'plan' is an agent step and requires an agent runner\."):
            StepExecutionContext(step=step, worktree_path=tmp_path, diagnostics=[])

    @pytest.mark.parametrize("with_runner", [True, False], ids=["with-runner", "without-runner"])
    @pytest.mark.parametrize("kind", ["command", "script", "internal", "ask"])
    def test_non_agent_step_constructs_with_or_without_runner(
        self, tmp_path: Path, kind: str, with_runner: bool
    ) -> None:
        runner = _runner if with_runner else None

        context = StepExecutionContext(
            step=_non_agent_step(kind), worktree_path=tmp_path, agent_runner=runner, diagnostics=[]
        )

        assert context.agent_runner is runner


class FrozenExecutionModelsTests:
    def test_assigning_a_step_execution_context_field_raises_frozen_instance_error(self, tmp_path: Path) -> None:
        context = StepExecutionContext(
            step=StepBuilder.command("echo ok").build(), worktree_path=tmp_path, diagnostics=[]
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            context.step_index = 2  # ty: ignore[invalid-assignment]  # intentional ill-typed test input: frozen-model write

    def test_assigning_an_execution_identity_field_raises_frozen_instance_error(self) -> None:
        identity = ExecutionIdentity(blueprint_name="ship")

        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.blueprint_name = "other"  # ty: ignore[invalid-assignment]  # intentional ill-typed test input: frozen-model write


_FAILED_STEP: dict[str, object] = {
    "step_id": "publish",
    "status": "failed",
    "exit_code": 0,
    "stdout": "",
    "stderr": "",
    "duration_seconds": 0.1,
    "error_message": "Failed to publish artifact 'plan': boom",
}


class StepRecordTests:
    def test_failed_record_carries_a_published_code_without_errors(self) -> None:
        """Build a failed record holding a code and its error_message, with no errors field."""
        record = StepRecord.model_validate({**_FAILED_STEP, "error_code": ErrorCode.INTERNAL_ERROR})

        assert (record.ok, record.error_code) == (False, ErrorCode.INTERNAL_ERROR)
        assert "errors" not in record.model_dump()

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"error_code": "INTERNAL_ERROR"}, id="plain-string-code"),
            pytest.param({"error_code": "CUSTOM_CODE"}, id="unknown-code"),
            pytest.param({"errors": [], "unexpected": 1}, id="non-legacy-extra-key"),
        ],
    )
    def test_unpublished_code_or_unknown_key_is_refused(self, fields: dict[str, object]) -> None:
        """Refuse a code outside the published set, and any extra key besides the legacy result keys."""
        with pytest.raises(ValidationError):
            StepRecord.model_validate({**_FAILED_STEP, **fields})
