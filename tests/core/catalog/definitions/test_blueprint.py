"""Contract tests for authored blueprint definition validators."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.catalog.definitions import BlueprintDefinition, LoopStepBlock, StepDefinition
from dovo.core.catalog.exceptions import BlueprintValidationError


class BlueprintDefinitionValidatorTests:
    """Defaults, loop validation and step shorthand normalization in BlueprintDefinition.from_document."""

    def test_defaults_on_failure_fills_only_steps_that_omit_it(self) -> None:
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
        raw: dict[str, object] = {"steps": [{"name": "Run Lint", "command": "ruff check ."}]}

        definition = BlueprintDefinition.from_document(raw, key="bp")

        assert len(definition.steps) == 1
        step = definition.steps[0]
        assert isinstance(step, StepDefinition)
        assert step.id == "run-lint"
        assert step.run == "ruff check ."


def _loop(**fields: object) -> dict[str, object]:
    """Return a loop block `fix` over one `check` step, with fields overriding or adding keys."""
    loop: dict[str, object] = {
        "id": "fix",
        "type": "loop",
        "until": ["steps.check.exit_code == 0"],
        "do": [{"id": "check", "run": "true"}],
    }
    return {**loop, **fields}


def _only_loop(raw: dict[str, object]) -> LoopStepBlock:
    """Return the one loop block raw defines."""
    loop = BlueprintDefinition.from_document(raw, key="bp").steps[0]
    assert isinstance(loop, LoopStepBlock)
    return loop


class LoopBlockDefinitionTests:
    """Loop blocks reject keys they do not define, accept `name`, and give `do` steps the top-level shorthands."""

    def test_unknown_keys_fail_naming_the_loop_and_every_key_sorted(self) -> None:
        raw: dict[str, object] = {"steps": [_loop(untill=["x"], for_each={"artifact": "plan"})]}

        with pytest.raises(BlueprintValidationError, match=r"Loop 'fix' has unknown keys: for_each, untill\."):
            BlueprintDefinition.from_document(raw, key="bp")

    def test_loop_name_is_accepted(self) -> None:
        loop = _only_loop({"steps": [_loop(name="Fix until green")]})

        assert loop.name == "Fix until green"

    @pytest.mark.parametrize(
        ("step", "expected_id"),
        [
            pytest.param({"name": "Run tests", "run": "uv run pytest"}, "run-tests", id="named"),
            pytest.param({"run": "echo fix"}, "fix-step-2", id="unnamed"),
            pytest.param({"name": "!!!", "run": "echo fix"}, "fix-step-2", id="name_without_slug"),
        ],
    )
    def test_do_step_id_derived_from_name_or_position(self, step: dict[str, object], expected_id: str) -> None:
        raw: dict[str, object] = {
            "steps": [_loop(until=["iteration.index == 1"], do=[{"id": "check", "run": "true"}, step])]
        }

        loop = _only_loop(raw)

        assert [do_step.id for do_step in loop.do] == ["check", expected_id]

    def test_do_step_command_shorthand_maps_to_run(self) -> None:
        loop = _only_loop({"steps": [_loop(do=[{"id": "check", "command": "echo hi"}])]})

        assert (loop.do[0].run, loop.do[0].command) == ("echo hi", None)

    def test_do_step_inherits_defaults_on_failure_unless_set(self) -> None:
        raw: dict[str, object] = {
            "defaults": {"on_failure": "continue"},
            "steps": [_loop(do=[{"id": "check", "run": "true"}, {"id": "own", "run": "x", "on_failure": "abort"}])],
        }

        loop = _only_loop(raw)

        assert [step.on_failure.action for step in loop.do] == [FailurePolicy.CONTINUE, FailurePolicy.ABORT]
