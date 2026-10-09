"""Contract tests for authored step definition models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.common.tool_policy import LEGACY_TOOLS_MESSAGE, ToolPolicy
from dovo.core.catalog.definitions import StepDefinition, StepType


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


class StepToolsContractTests:
    def test_legacy_string_list_raises_with_migration_message(self) -> None:
        """[tier-1/unit] StepDefinition.model_validate: tools=['shell'] raises ValidationError containing LEGACY_TOOLS_MESSAGE."""
        with pytest.raises(ValidationError, match=LEGACY_TOOLS_MESSAGE):
            StepDefinition.model_validate({"id": "s", "type": "agent", "prompt": "p", "tools": ["shell"]})

    def test_omitted_tools_is_none_and_unset_while_empty_object_is_set_empty_policy(self) -> None:
        """[tier-1/unit] StepDefinition: omitted -> tools is None and 'tools' not in model_fields_set; tools={} -> ToolPolicy() and 'tools' in model_fields_set."""
        omitted = StepDefinition.model_validate({"id": "s", "type": "agent", "prompt": "p"})
        explicit = StepDefinition.model_validate({"id": "s", "type": "agent", "prompt": "p", "tools": {}})

        assert (omitted.tools, "tools" in omitted.model_fields_set) == (None, False)
        assert (explicit.tools, "tools" in explicit.model_fields_set) == (ToolPolicy(), True)

    def test_run_step_with_empty_tools_object_is_rejected_but_omitted_is_accepted(self) -> None:
        """[tier-1/unit] StepDefinition: run with tools={} raises ValidationError naming 'tools'; run without tools validates."""
        assert StepDefinition.model_validate({"id": "s", "run": "r"}).run == "r"
        with pytest.raises(ValidationError, match="tools"):
            StepDefinition.model_validate({"id": "s", "run": "r", "tools": {}})
