"""Contract tests for authored blueprint definition validators."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy
from dovo.core.catalog.definitions import BlueprintDefinition, IfStepBlock, LoopStepBlock, StepDefinition
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


def _route(**fields: object) -> dict[str, object]:
    """Return an if block `route` after step `check`, with fields overriding or adding keys."""
    block: dict[str, object] = {
        "id": "route",
        "type": "if",
        "condition": ["steps.check.exit_code == 0"],
        "then": [{"id": "ship", "run": "true"}],
    }
    return {**block, **fields}


def _only_block(raw_steps: list[object], **document: object) -> IfStepBlock:
    """Return the if block of a blueprint whose steps are a `check` step followed by raw_steps."""
    raw: dict[str, object] = {**document, "steps": [{"id": "check", "run": "true"}, *raw_steps]}
    block = BlueprintDefinition.from_document(raw, key="bp").steps[1]
    assert isinstance(block, IfStepBlock)
    return block


class IfBlockDefinitionTests:
    """If blocks parse at the top level, give branch steps the top-level shorthands, and reject what O-10 forbids."""

    def test_branch_steps_get_derived_ids_mapped_command_and_inherited_on_failure(self) -> None:
        then = [{"run": "a"}, {"name": "Ship it", "command": "b"}]
        otherwise = [{"run": "c", "on_failure": "abort"}]

        block = _only_block([_route(then=then, **{"else": otherwise})], defaults={"on_failure": "continue"})

        assert [(step.id, step.run) for step in block.branch("then")] == [("route-then-step-1", "a"), ("ship-it", "b")]
        assert [step.id for step in block.branch("else")] == ["route-else-step-1"]
        assert [step.on_failure.action for step in [*block.branch("then"), *block.branch("else")]] == [
            FailurePolicy.CONTINUE,
            FailurePolicy.CONTINUE,
            FailurePolicy.ABORT,
        ]

    def test_else_is_optional(self) -> None:
        block = _only_block([_route(name="Route the plan")])

        assert (block.name, block.else_, block.branch("else")) == ("Route the plan", None, [])

    @pytest.mark.parametrize(
        ("block", "message"),
        [
            pytest.param(
                _route(condition=[]), "If block 'route': 'condition' must list at least one", id="empty_condition"
            ),
            pytest.param(_route(then=[]), "If block 'route': 'then' must list at least one step", id="empty_then"),
            pytest.param(
                _route(**{"else": []}), "If block 'route': 'else' must list at least one step", id="empty_else"
            ),
            pytest.param(
                _route(then=[_loop()]),
                "If block 'route': step 1 of 'then' is a 'type: loop' block; 'then' holds only steps",
                id="loop_in_then",
            ),
            pytest.param(
                _route(**{"else": [{"id": "inner", "run": "x"}, _route(id="inner-if")]}),
                "If block 'route': step 2 of 'else' is a 'type: if' block; 'else' holds only steps",
                id="if_in_else",
            ),
            pytest.param(_route(elif_=["x"]), r"If block 'route' has unknown keys: elif_\.", id="unknown_key"),
        ],
    )
    def test_invalid_block_fails_naming_it(self, block: dict[str, object], message: str) -> None:
        raw: dict[str, object] = {"steps": [{"id": "check", "run": "true"}, block]}

        with pytest.raises(BlueprintValidationError, match=message):
            BlueprintDefinition.from_document(raw, key="bp")

    def test_if_block_inside_a_loop_fails_naming_the_loop(self) -> None:
        raw: dict[str, object] = {"steps": [_loop(do=[{"id": "check", "run": "true"}, _route()])]}

        with pytest.raises(BlueprintValidationError, match="Loop 'fix': step 2 of 'do' is a 'type: if' block"):
            BlueprintDefinition.from_document(raw, key="bp")
