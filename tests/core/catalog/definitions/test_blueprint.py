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


def _schema_error(raw: dict[str, object]) -> str:
    """Return the message of the BlueprintValidationError raised for raw."""
    with pytest.raises(BlueprintValidationError) as caught:
        BlueprintDefinition.from_document(raw, key="bp")
    return str(caught.value)


def _loop(**fields: object) -> dict[str, object]:
    """Return a loop block over one `check` step, with fields overriding or adding keys."""
    loop: dict[str, object] = {
        "id": "phases",
        "type": "loop",
        "until": ["steps.check.exit_code == 0"],
        "do": [{"id": "check", "run": "true"}],
    }
    return {**loop, **fields}


class LoopBlockDefinitionTests:
    """Loop blocks reject keys they do not define and normalize the steps in their `do` list."""

    def test_unknown_keys_fail_naming_the_loop_and_every_key_sorted(self) -> None:
        raw: dict[str, object] = {"steps": [_loop(for_each={"artifact": "plan"}, untill=["x"])]}

        assert "Loop 'phases' has unknown key(s): for_each, untill." in _schema_error(raw)

    def test_do_steps_get_derived_ids_mapped_command_and_inherited_on_failure(self) -> None:
        raw: dict[str, object] = {
            "defaults": {"on_failure": "continue"},
            "steps": [
                _loop(
                    until=["steps.run-tests.exit_code == 0"],
                    do=[{"name": "Run tests", "run": "uv run pytest"}, {"run": "echo fix"}, {"command": "echo hi"}],
                )
            ],
        }

        loop = BlueprintDefinition.from_document(raw, key="bp").steps[0]

        assert isinstance(loop, LoopStepBlock)
        assert [(step.id, step.run, step.on_failure.action) for step in loop.do] == [
            ("run-tests", "uv run pytest", FailurePolicy.CONTINUE),
            ("phases-step-2", "echo fix", FailurePolicy.CONTINUE),
            ("phases-step-3", "echo hi", FailurePolicy.CONTINUE),
        ]

    def test_if_block_inside_do_fails_naming_the_loop(self) -> None:
        raw: dict[str, object] = {
            "steps": [_loop(do=[{"id": "check", "run": "true"}, {"id": "pick", "type": "if", "condition": ["a"]}])]
        }

        assert "Loop 'phases': 'do' entry 'pick' is a 'if' block; 'do' holds only steps." in _schema_error(raw)


class WhenGuardDefinitionTests:
    """`when` lists validate like loop `until` conditions, against earlier steps of the same or an enclosing scope."""

    def test_when_parses_on_every_step_kind(self) -> None:
        raw: dict[str, object] = {
            "steps": [
                {"id": "check", "run": "true"},
                {"id": "fix", "run": "make fix", "when": ["steps.check.exit_code != 0"]},
                {
                    "id": "ask",
                    "type": "ask",
                    "when": ["steps.check.exit_code != 0"],
                    "interaction": {"mode": "confirm", "message": "Go?"},
                },
                _loop(
                    when=["steps.fix.exit_code == 0"],
                    until=["steps.again.exit_code == 0"],
                    do=[{"id": "again", "run": "true", "when": ["iteration.index > 1", "steps.check.exit_code == 0"]}],
                ),
            ]
        }

        steps = BlueprintDefinition.from_document(raw, key="bp").steps

        loop = steps[3]
        assert isinstance(loop, LoopStepBlock)
        assert [getattr(step, "when", None) for step in steps] == [
            None,
            ["steps.check.exit_code != 0"],
            ["steps.check.exit_code != 0"],
            ["steps.fix.exit_code == 0"],
        ]
        assert loop.do[0].when == ["iteration.index > 1", "steps.check.exit_code == 0"]

    @pytest.mark.parametrize(
        ("steps", "message"),
        [
            pytest.param(
                [{"id": "a", "run": "true", "when": []}],
                "Step 'a': 'when' must list at least one condition.",
                id="empty",
            ),
            pytest.param(
                [{"id": "a", "run": "true", "when": ["steps.a.exit_code ="]}],
                "Step 'a': Invalid condition expression 'steps.a.exit_code ='.",
                id="invalid-expression",
            ),
            pytest.param(
                [{"id": "a", "run": "true", "when": ["steps.b.exit_code == 0"]}, {"id": "b", "run": "true"}],
                "Step 'a': Step id 'b' referenced in when condition 'steps.b.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="later-step",
            ),
            pytest.param(
                [_loop(), {"id": "after", "run": "true", "when": ["steps.check.exit_code == 0"]}],
                "Step 'after': Step id 'check' referenced in when condition 'steps.check.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="inner-loop-step",
            ),
            pytest.param(
                [{"id": "a", "run": "true", "when": ["iteration.index == 1"]}],
                "Step 'a': Operand 'iteration.index' in when condition 'iteration.index == 1' "
                "is only available inside a loop's 'do'.",
                id="iteration-outside-loop",
            ),
            pytest.param(
                [_loop(when=["steps.check.exit_code == 0"])],
                "Loop 'phases': Step id 'check' referenced in when condition 'steps.check.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="loop-guard-on-own-body",
            ),
        ],
    )
    def test_invalid_when_fails_naming_the_step(self, steps: list[object], message: str) -> None:
        assert message in _schema_error({"steps": steps})


def _if_block(**fields: object) -> dict[str, object]:
    """Return an if block on step `check` with one `then` and one `else` step, with fields overriding keys."""
    block: dict[str, object] = {
        "id": "route",
        "type": "if",
        "condition": ["steps.check.exit_code == 0"],
        "then": [{"id": "ship", "run": "echo ship"}],
        "else": [{"id": "fix", "run": "echo fix"}],
    }
    return {**block, **fields}


class IfBlockDefinitionTests:
    """`type: if` blocks parse at the top level and reject empty lists, nested blocks and out-of-scope references."""

    def test_if_block_parses_with_normalized_branch_steps(self) -> None:
        raw: dict[str, object] = {
            "defaults": {"on_failure": "continue"},
            "steps": [
                {"id": "check", "run": "true"},
                _if_block(then=[{"command": "echo t"}], **{"else": [{"name": "Fix It", "run": "f"}]}),
            ],
        }

        block = BlueprintDefinition.from_document(raw, key="bp").steps[1]

        assert isinstance(block, IfStepBlock)
        assert block.condition == ["steps.check.exit_code == 0"]
        assert [(step.id, step.run, step.on_failure.action) for step in block.then] == [
            ("route-then-step-1", "echo t", FailurePolicy.CONTINUE)
        ]
        assert [step.id for step in block.branch("else")] == ["fix-it"]

    def test_later_step_may_reference_a_branch_step(self) -> None:
        raw: dict[str, object] = {
            "steps": [
                {"id": "check", "run": "true"},
                _if_block(),
                {"id": "report", "run": "true", "when": ["steps.fix.exit_code == 0"]},
            ]
        }

        steps = BlueprintDefinition.from_document(raw, key="bp").steps

        assert [step.id for step in steps] == ["check", "route", "report"]

    @pytest.mark.parametrize(
        ("block", "message"),
        [
            pytest.param(
                _if_block(condition=[]),
                "If block 'route': 'condition' must list at least one condition.",
                id="empty-condition",
            ),
            pytest.param(
                _if_block(then=[]),
                "If block 'route': 'then' must list at least one step.",
                id="empty-then",
            ),
            pytest.param(
                _if_block(condition=["oops"]),
                "If block 'route': Invalid condition expression 'oops'.",
                id="invalid-expression",
            ),
            pytest.param(
                _if_block(condition=["steps.ship.exit_code == 0"]),
                "If block 'route': Step id 'ship' referenced in if condition 'steps.ship.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="condition-on-own-branch",
            ),
            pytest.param(
                _if_block(**{"else": [{"id": "fix", "run": "f", "when": ["steps.ship.exit_code == 0"]}]}),
                "Step 'fix': Step id 'ship' referenced in when condition 'steps.ship.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="else-step-on-then-step",
            ),
            pytest.param(
                _if_block(then=[{"id": "inner", "type": "loop", "until": ["x"], "do": []}]),
                "If block 'route': 'then' entry 'inner' is a 'loop' block; 'then' holds only steps.",
                id="loop-in-branch",
            ),
            pytest.param(
                _if_block(**{"else": [{"id": "inner", "type": "if"}]}),
                "If block 'route': 'else' entry 'inner' is a 'if' block; 'else' holds only steps.",
                id="if-in-branch",
            ),
        ],
    )
    def test_invalid_if_block_fails_naming_it(self, block: dict[str, object], message: str) -> None:
        assert message in _schema_error({"steps": [{"id": "check", "run": "true"}, block]})
