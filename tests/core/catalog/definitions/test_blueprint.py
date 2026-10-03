"""Contract tests for authored blueprint definition validators."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.catalog.definitions import BlueprintDefinition, StepDefinition
from dovo.core.catalog.exceptions import BlueprintValidationError


class BlueprintDefinitionValidatorTests:
    """[tier-1/unit] BlueprintDefinition.from_document: defaults, loop validation, and step shorthand normalization."""

    def test_defaults_on_failure_fills_only_steps_that_omit_it(self) -> None:
        """[tier-1/unit] BlueprintDefinition.from_document: defaults.on_failure=continue gives an omitting step on_failure.action == FailurePolicy.CONTINUE and leaves a step with explicit on_failure: abort at FailurePolicy.ABORT."""
        raw: dict[str, object] = {
            "defaults": {"on_failure": "continue"},
            "steps": [
                {"id": "omits", "run": "echo a"},
                {"id": "explicit", "run": "echo b", "on_failure": "abort"},
            ],
        }

        definition = BlueprintDefinition.from_document(raw, key="bp")

        omits, explicit = definition.steps
        assert isinstance(omits, StepDefinition)
        assert isinstance(explicit, StepDefinition)
        assert omits.on_failure.action == FailurePolicy.CONTINUE
        assert explicit.on_failure.action == FailurePolicy.ABORT

    def test_loop_until_unknown_step_id_raises_blueprint_validation_error(self) -> None:
        """[tier-1/unit] BlueprintDefinition.from_document: a loop whose until references steps.ghost.exit_code with do steps [build] raises BlueprintValidationError whose message contains "Step id 'ghost' referenced in until condition"."""
        raw: dict[str, object] = {
            "steps": [
                {
                    "id": "retry",
                    "type": "loop",
                    "until": ["steps.ghost.exit_code == 0"],
                    "do": [{"id": "build", "run": "make"}],
                }
            ],
        }

        with pytest.raises(BlueprintValidationError, match="Step id 'ghost' referenced in until condition"):
            BlueprintDefinition.from_document(raw, key="bp")

    def test_step_without_id_or_mode_gets_default_id_and_command_maps_to_run(self) -> None:
        """[tier-1/unit] BlueprintDefinition.from_document: steps [{"name": "Run Lint", "command": "ruff check ."}] parse to one StepDefinition with id == "run-lint" and run == "ruff check ."."""
        raw: dict[str, object] = {"steps": [{"name": "Run Lint", "command": "ruff check ."}]}

        definition = BlueprintDefinition.from_document(raw, key="bp")

        assert len(definition.steps) == 1
        step = definition.steps[0]
        assert isinstance(step, StepDefinition)
        assert step.id == "run-lint"
        assert step.run == "ruff check ."
