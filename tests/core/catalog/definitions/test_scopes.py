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
                "Step 'a': Invalid condition expression 'not a condition'. "
                "Expected '<operand> <operator> <operand>' with operators: !=, <, <=, ==, >, >=, contains.",
                id="malformed",
            ),
            pytest.param(
                [{"id": "a", "run": "x"}, {"id": "b", "run": "y", "when": ["steps.a.code == 0"]}],
                "Step 'b': Unknown step field 'code' in 'steps.a.code'. "
                "Allowed fields: exit_code, outputs, status, stdout.",
                id="unknown_field",
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
