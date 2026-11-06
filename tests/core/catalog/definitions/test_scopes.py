"""Contract tests for the authoring-time scope check of `when` lists."""

from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.catalog.definitions.scopes import condition_reference_errors


def _errors(steps: list[dict[str, object]]) -> list[str]:
    """Return the scope errors of a blueprint built from steps."""
    return condition_reference_errors(BlueprintDefinition.model_validate({"name": "bp", "steps": steps}).steps)


def _loop(when: list[str] | None = None, do: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Return a loop block 'fix' whose 'do' runs check and the given steps."""
    loop: dict[str, object] = {
        "id": "fix",
        "type": "loop",
        "until": ["steps.check.exit_code == 0"],
        "do": [{"id": "check", "run": "true"}, *(do or [])],
    }
    if when is not None:
        loop["when"] = when
    return loop


class ConditionScopeTests:
    @pytest.mark.parametrize(
        ("steps", "expected"),
        [
            pytest.param(
                [{"id": "a", "run": "x", "when": ["steps.b.exit_code == 0"]}, {"id": "b", "run": "y"}],
                "Step 'a': Step id 'b' referenced in when condition 'steps.b.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="later_step",
            ),
            pytest.param(
                [{"id": "a", "run": "x", "when": ["steps.a.exit_code == 0"]}],
                "Step 'a': Step id 'a' referenced in when condition 'steps.a.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="own_id",
            ),
            pytest.param(
                [_loop(), {"id": "after", "run": "x", "when": ["steps.fix.exit_code == 0"]}],
                "Step 'after': Step id 'fix' referenced in when condition 'steps.fix.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="loop_id",
            ),
            pytest.param(
                [_loop(), {"id": "after", "run": "x", "when": ["steps.check.exit_code == 0"]}],
                "Step 'after': Step id 'check' referenced in when condition 'steps.check.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="do_step_from_top_level",
            ),
            pytest.param(
                [_loop(when=["steps.check.exit_code == 0"])],
                "Loop 'fix': Step id 'check' referenced in when condition 'steps.check.exit_code == 0' "
                "not found in earlier steps (allowed: none).",
                id="loop_guard_on_its_own_do_step",
            ),
            pytest.param(
                [
                    _loop(
                        do=[
                            {"id": "first", "run": "x", "when": ["steps.later.exit_code == 0"]},
                            {"id": "later", "run": "y"},
                        ]
                    )
                ],
                "Step 'first': Step id 'later' referenced in when condition 'steps.later.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="later_do_step",
            ),
            pytest.param(
                [{"id": "a", "run": "x", "when": ["iteration.index > 1"]}],
                "Step 'a': Operand 'iteration.index' in when condition 'iteration.index > 1' "
                "is only available inside a loop's 'do'.",
                id="iteration_outside_do",
            ),
            pytest.param(
                [{"id": "a", "run": "x", "when": ["not a condition"]}],
                "Step 'a': Invalid expression 'not a condition': unquoted text 'not a condition' at column 1; "
                "quote a value of more than one word.",
                id="malformed",
            ),
            pytest.param(
                [{"id": "a", "run": "x"}, {"id": "b", "run": "y", "when": ["steps.a.code == 0"]}],
                "Step 'b': Unknown step field 'code' in 'steps.a.code'. "
                "Allowed fields: error_code, exit_code, outputs, status, stdout.",
                id="unknown_field",
            ),
            pytest.param(
                [{"id": "a", "run": "x"}, {"id": "b", "run": "y", "when": ["steps.a.exit_code =="]}],
                "Step 'b': Invalid expression 'steps.a.exit_code ==': missing value after '=='.",
                id="when_syntax_error",
            ),
            pytest.param(
                [
                    {"id": "a", "run": "x"},
                    {
                        "id": "gate",
                        "type": "if",
                        "condition": ["steps.a.status == inputs.mode"],
                        "then": [{"id": "go", "run": "y"}],
                    },
                ],
                "If block 'gate': Operand 'inputs.mode' in if condition 'steps.a.status == inputs.mode' "
                "reads 'inputs', which a condition cannot read. Readable roots: artifacts, steps.",
                id="if_inputs_root",
            ),
        ],
    )
    def test_invalid_reference_reports_one_message_naming_the_owner(
        self, steps: list[dict[str, object]], expected: str
    ) -> None:
        assert _errors(steps) == [expected]

    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param(
                [{"id": "a", "run": "x"}, {"id": "b", "run": "y", "when": ["steps.a.exit_code == 0"]}],
                id="earlier_top_level",
            ),
            pytest.param(
                [_loop(do=[{"id": "fix-it", "run": "y", "when": ["steps.check.exit_code != 0"]}])],
                id="earlier_do_step",
            ),
            pytest.param(
                [
                    {"id": "a", "run": "x"},
                    _loop(
                        when=["steps.a.exit_code == 0"],
                        do=[{"id": "b", "run": "y", "when": ["steps.a.exit_code == 0", "iteration.index > 1"]}],
                    ),
                ],
                id="top_level_and_iteration_from_do",
            ),
            pytest.param(
                [
                    {
                        "id": "pick",
                        "type": "ask",
                        "interaction": {
                            "mode": "multiselect",
                            "message": "Pick",
                            "options": [{"value": "x", "label": "X"}],
                        },
                    },
                    {"id": "use", "run": "y", "when": ["steps.pick.outputs.selected_count > 0"]},
                ],
                id="ask_output",
            ),
        ],
    )
    def test_valid_reference_reports_no_errors(self, steps: list[dict[str, object]]) -> None:
        assert _errors(steps) == []

    def test_every_invalid_expression_is_reported_separately(self) -> None:
        steps: list[dict[str, object]] = [
            {"id": "a", "run": "x", "when": ["steps.b.exit_code == 0", "steps.c.exit_code == 0"]}
        ]

        assert _errors(steps) == [
            "Step 'a': Step id 'b' referenced in when condition 'steps.b.exit_code == 0' "
            "not found in earlier steps (allowed: none).",
            "Step 'a': Step id 'c' referenced in when condition 'steps.c.exit_code == 0' "
            "not found in earlier steps (allowed: none).",
        ]


def _route(**fields: object) -> dict[str, object]:
    """Return an if block 'route' whose branches run 'fix' then 'verify', or 'ship'."""
    block: dict[str, object] = {
        "id": "route",
        "type": "if",
        "condition": ["steps.check.exit_code == 0"],
        "then": [{"id": "fix", "run": "x"}, {"id": "verify", "run": "y"}],
        "else": [{"id": "ship", "run": "z"}],
    }
    return {**block, **fields}


_CHECK: dict[str, object] = {"id": "check", "run": "true"}


class IfBlockScopeTests:
    @pytest.mark.parametrize(
        ("steps", "expected"),
        [
            pytest.param(
                [_CHECK, _route(condition=["steps.fix.exit_code == 0"])],
                "If block 'route': Step id 'fix' referenced in if condition 'steps.fix.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="condition_on_branch_step",
            ),
            pytest.param(
                [_CHECK, _route(condition=["steps.after.exit_code == 0"]), {"id": "after", "run": "x"}],
                "If block 'route': Step id 'after' referenced in if condition 'steps.after.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="condition_on_later_step",
            ),
            pytest.param(
                [_CHECK, _route(**{"else": [{"id": "ship", "run": "z", "when": ["steps.fix.exit_code == 0"]}]})],
                "Step 'ship': Step id 'fix' referenced in when condition 'steps.fix.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="else_step_on_then_step",
            ),
            pytest.param(
                [
                    _CHECK,
                    _route(
                        then=[
                            {"id": "fix", "run": "x", "when": ["steps.verify.exit_code == 0"]},
                            {"id": "verify", "run": "y"},
                        ]
                    ),
                ],
                "Step 'fix': Step id 'verify' referenced in when condition 'steps.verify.exit_code == 0' "
                "not found in earlier steps (allowed: check).",
                id="then_step_on_later_then_step",
            ),
            pytest.param(
                [_CHECK, _route(), {"id": "after", "run": "x", "when": ["steps.route.exit_code == 0"]}],
                "Step 'after': Step id 'route' referenced in when condition 'steps.route.exit_code == 0' "
                "not found in earlier steps (allowed: check, fix, ship, verify).",
                id="block_id",
            ),
            pytest.param(
                [_CHECK, _route(condition=["iteration.index > 1"])],
                "If block 'route': Operand 'iteration.index' in if condition 'iteration.index > 1' "
                "is only available inside a loop's 'do'.",
                id="iteration_in_condition",
            ),
        ],
    )
    def test_invalid_reference_reports_one_message_naming_the_owner(
        self, steps: list[dict[str, object]], expected: str
    ) -> None:
        assert _errors(steps) == [expected]

    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param([_CHECK, _route()], id="condition_on_earlier_step"),
            pytest.param(
                [
                    _CHECK,
                    _route(
                        then=[
                            {"id": "fix", "run": "x", "when": ["steps.check.exit_code == 0"]},
                            {"id": "verify", "run": "y", "when": ["steps.fix.exit_code == 0"]},
                        ]
                    ),
                ],
                id="branch_steps_on_step_before_block_and_earlier_branch_step",
            ),
            pytest.param(
                [
                    _CHECK,
                    _route(),
                    {"id": "after", "run": "x", "when": ["steps.fix.exit_code == 0", "steps.ship.exit_code == 0"]},
                ],
                id="later_step_on_either_branch",
            ),
        ],
    )
    def test_valid_reference_reports_no_errors(self, steps: list[dict[str, object]]) -> None:
        assert _errors(steps) == []


def _publishes(step_id: str, name: str, **fields: object) -> dict[str, object]:
    """Return a step step_id that publishes artifact name."""
    return {"id": step_id, "run": "x", "artifacts": [{"name": name, "path": f"{name}.json"}], **fields}


_PLAN = _publishes("plan", "plan")
_GHOST = 'artifacts.ghost.content == "x"'
_GHOST_MESSAGE = (
    "Artifact 'ghost' read in {clause} condition 'artifacts.ghost.content == \"x\"' is not published by "
    "any step's 'artifacts:' in the blueprint (published: plan)."
)


class ArtifactPublisherScopeTests:
    @pytest.mark.parametrize(
        ("steps", "owner", "clause"),
        [
            pytest.param([_PLAN, {"id": "a", "run": "x", "when": [_GHOST]}], "Step 'a'", "when", id="top_level_when"),
            pytest.param([_PLAN, _loop(when=[_GHOST])], "Loop 'fix'", "when", id="loop_when"),
            pytest.param(
                [_PLAN, _loop(do=[{"id": "b", "run": "y", "when": [_GHOST]}])], "Step 'b'", "when", id="do_step_when"
            ),
            pytest.param([_PLAN, _route(condition=[_GHOST])], "If block 'route'", "if", id="if_condition"),
            pytest.param(
                [
                    _PLAN,
                    _route(
                        condition=["steps.plan.exit_code == 0"],
                        **{"else": [{"id": "ship", "run": "z", "when": [_GHOST]}]},
                    ),
                ],
                "Step 'ship'",
                "when",
                id="branch_step_when",
            ),
        ],
    )
    def test_unpublished_artifact_reports_one_message_naming_the_owner(
        self, steps: list[dict[str, object]], owner: str, clause: str
    ) -> None:
        assert _errors(steps) == [f"{owner}: {_GHOST_MESSAGE.format(clause=clause)}"]

    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param([{"id": "a", "run": "x", "when": ["artifacts.plan.revision >= 1"]}, _PLAN], id="later_step"),
            pytest.param(
                [{"id": "a", "run": "x", "when": ["artifacts.plan.revision >= 1"]}, _loop(do=[_PLAN])],
                id="loop_do_step",
            ),
            pytest.param(
                [_route(condition=["artifacts.plan.revision >= 1"], **{"else": [_PLAN]})], id="if_branch_step"
            ),
            pytest.param(
                [
                    {"id": "a", "run": "x", "when": ["artifacts.plan.revision >= 1"]},
                    {"id": "plan", "uses": "plan-step", "artifacts": [{"name": "plan", "path": "plan.json"}]},
                ],
                id="uses_step",
            ),
        ],
    )
    def test_artifact_published_anywhere_in_the_blueprint_passes(self, steps: list[dict[str, object]]) -> None:
        assert _errors(steps) == []
