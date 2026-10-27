"""Contract tests for step execution models."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from pydantic import ValidationError

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
            pytest.param(AgentResponseStatus.UNFIXABLE, False, id="unfixable"),
            pytest.param(AgentResponseStatus.TIMEOUT, False, id="timeout"),
            pytest.param(AgentResponseStatus.PROVIDER_ERROR, False, id="provider_error"),
            pytest.param(AgentResponseStatus.BLOCKED, False, id="blocked"),
        ],
    )
    def test_succeeded_is_true_only_for_statuses_with_a_zero_exit_code(
        self, status: AgentResponseStatus, expected: bool
    ) -> None:
        assert AgentStepSummary(status=status).succeeded is expected


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
