"""Contract tests for step models: StepDefinition artifacts and type=internal shape, and AgentStepSummary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worktree.core.agents import AgentResponseStatus
from worktree.core.step.models import AgentStepSummary, StepDefinition, StepType


class StepDefinitionArtifactsFieldTests:
    """[tier-1/unit] StepDefinition.artifacts: default, validation, and type=internal shape."""

    def test_artifacts_field_defaults_to_empty_list(self) -> None:
        """[tier-1/unit] StepDefinition: a step with no artifacts: key parses with artifacts == []."""
        step = StepDefinition(id="s1", type=StepType.COMMAND, command="echo hi")

        assert step.artifacts == []

    @pytest.mark.parametrize(
        "artifacts_payload",
        [
            pytest.param([{"path": "dist/*.whl"}], id="missing_name"),
            pytest.param([{"name": "dist-packages"}], id="missing_path"),
        ],
    )
    def test_artifacts_field_rejects_missing_name_or_path(self, artifacts_payload: list[dict[str, str]]) -> None:
        """[tier-1/unit] StepDefinition: an artifacts: entry missing name or path raises a pydantic ValidationError."""
        with pytest.raises(ValidationError):
            StepDefinition(
                id="s1",
                type=StepType.COMMAND,
                command="echo hi",
                artifacts=artifacts_payload,  # pyright: ignore[reportArgumentType]  # intentional ill-typed input: the raised ValidationError is this test's subject
            )

    def test_internal_type_without_command_raises_validation_error(self) -> None:
        """[tier-1/unit] StepDefinition: type=internal with no command string raises a pydantic ValidationError."""
        with pytest.raises(ValidationError):
            StepDefinition(id="s1", type=StepType.INTERNAL)


class AgentStepSummaryTests:
    """[tier-1/unit] AgentStepSummary: stdout wire shape of an agent step."""

    def test_defaults_and_field_order_serialize_to_exact_json(self) -> None:
        """[tier-1/unit] AgentStepSummary: only status given serializes to the exact compact JSON with null summary and unfixable_reason and an empty touched_files, in declared field order."""
        summary = AgentStepSummary(status=AgentResponseStatus.NO_OP)

        assert (
            summary.model_dump_json() == '{"status":"no_op","summary":null,"unfixable_reason":null,"touched_files":[]}'
        )

    def test_unknown_field_raises_validation_error(self) -> None:
        """[tier-1/unit] AgentStepSummary: an unknown field raises a pydantic ValidationError."""
        with pytest.raises(ValidationError):
            AgentStepSummary.model_validate({"status": "no_op", "extra": 1})
