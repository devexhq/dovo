from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import validate_condition_expression
from dovo.core.catalog.definitions.conditions import ConditionScope

_UNTIL_SCOPE = ConditionScope(known_step_ids=frozenset({"a"}))
_WHEN_SCOPE = ConditionScope(
    clause="when", known_step_ids=frozenset({"a"}), scope_label="earlier steps", allow_iteration=False
)


class ConditionExpressionValidatorTests:
    """Unit tests verifying semantic validation of until condition expressions."""

    def test_validate_condition_expression_detects_unknown_step_ids(self) -> None:
        expression = "steps.unknown_step.exit_code == 0"
        errors = validate_condition_expression(expression, known_step_ids={"build"})
        assert errors == [
            "Step id 'unknown_step' referenced in until condition 'steps.unknown_step.exit_code == 0' not found in loop 'do' steps (allowed: build)."
        ]

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            pytest.param("", "Invalid expression '': empty expression.", id="empty"),
            pytest.param(
                "steps.a.exit_code ==",
                "Invalid expression 'steps.a.exit_code ==': missing value after '=='.",
                id="dangling_operator",
            ),
            pytest.param(
                'steps.a.stdout == "abc',
                "Invalid expression 'steps.a.stdout == \"abc': unterminated string at column 19.",
                id="unterminated_string",
            ),
            pytest.param(
                "steps.a.status == needs edits",
                "Invalid expression 'steps.a.status == needs edits': "
                "unquoted text 'needs edits' at column 19; quote a value of more than one word.",
                id="multi_word_bare_value",
            ),
            pytest.param(
                "steps.a.exit_code == == 0",
                "Invalid expression 'steps.a.exit_code == == 0': unexpected '==' at column 22.",
                id="doubled_operator",
            ),
        ],
    )
    def test_syntax_error_reports_invalid_expression(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, known_step_ids={"a"}) == [message]

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("not a condition", id="bare_words"),
            pytest.param("steps.a.exit_code", id="lone_path"),
            pytest.param("done!", id="punctuated_word"),
            pytest.param('"abc', id="tokenize_error_without_operator"),
            pytest.param("1e3", id="unsupported_number_without_operator"),
        ],
    )
    def test_entry_without_an_operator_keeps_the_existing_message(self, expression: str) -> None:
        assert validate_condition_expression(expression, known_step_ids={"a"}) == [
            f"Invalid condition expression '{expression}'. "
            "Expected '<operand> <operator> <operand>' with operators: !=, <, <=, ==, >, >=, contains."
        ]

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param("steps.a.outputs['x.y'] == 1", id="bracket_key"),
            pytest.param("steps['a'].exit_code == 0", id="bracket_step_id"),
            pytest.param('"a == b" == steps.a.stdout', id="quoted_operator_on_the_left"),
            pytest.param('steps.contains.status == "x"', id="contains_step_id"),
        ],
    )
    def test_form_the_grammar_reads_reports_no_errors(self, expression: str) -> None:
        assert validate_condition_expression(expression, known_step_ids={"a", "contains"}) == []

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            pytest.param(
                "steps.a == 1",
                "Invalid step reference 'steps.a' in condition. Expected 'steps.<step_id>.<field>'.",
                id="short_step_reference",
            ),
            pytest.param(
                '"x" == "y"',
                'Condition \'"x" == "y"\' must reference at least one dynamic operand '
                "('steps.<id>.<field>' or 'iteration.index').",
                id="no_dynamic_operand",
            ),
            pytest.param(
                '"x" == steps',
                "Condition '\"x\" == steps' must reference at least one dynamic operand "
                "('steps.<id>.<field>' or 'iteration.index').",
                id="quoted_value_against_lone_steps",
            ),
        ],
    )
    def test_existing_operand_message_is_unchanged(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, known_step_ids={"a"}) == [message]

    @pytest.mark.parametrize(
        ("expression", "scope", "message"),
        [
            pytest.param(
                "steps.a.status == inputs.mode",
                _UNTIL_SCOPE,
                "Operand 'inputs.mode' in until condition 'steps.a.status == inputs.mode' reads 'inputs', "
                "which a condition cannot read. Readable roots: iteration, steps.",
                id="inputs_in_until",
            ),
            pytest.param(
                "steps.a.status == inputs.mode",
                _WHEN_SCOPE,
                "Operand 'inputs.mode' in when condition 'steps.a.status == inputs.mode' reads 'inputs', "
                "which a condition cannot read. Readable roots: steps.",
                id="inputs_in_when",
            ),
            pytest.param(
                'artifacts.plan.content == "x"',
                _WHEN_SCOPE,
                "Operand 'artifacts.plan.content' in when condition 'artifacts.plan.content == \"x\"' "
                "reads 'artifacts', which a condition cannot read. Readable roots: steps.",
                id="artifacts_root",
            ),
            pytest.param(
                "steps.a.stdout == a.py",
                _UNTIL_SCOPE,
                "Operand 'a.py' in until condition 'steps.a.stdout == a.py' reads 'a', "
                "which a condition cannot read. Readable roots: iteration, steps.",
                id="dotted_bare_value",
            ),
            pytest.param(
                'inputs.mode == "x"',
                _UNTIL_SCOPE,
                "Operand 'inputs.mode' in until condition 'inputs.mode == \"x\"' reads 'inputs', "
                "which a condition cannot read. Readable roots: iteration, steps.",
                id="inputs_only",
            ),
        ],
    )
    def test_unreadable_root_is_rejected(self, expression: str, scope: ConditionScope, message: str) -> None:
        assert validate_condition_expression(expression, scope=scope) == [message]

    @pytest.mark.parametrize(
        ("expression", "step_id"),
        [
            pytest.param('steps.fix.outputs.status != "unfixable"', "fix", id="quoted_status"),
            pytest.param("steps.a.status == 'success'", "a", id="single_quoted"),
            pytest.param("steps.a.status == done", "a", id="bare_word"),
            pytest.param("steps.build.stdout contains success", "build", id="contains_bare_word"),
            pytest.param("steps.phase.outputs.remaining == 0", "phase", id="numeric_output"),
            pytest.param("steps.a.outputs.score >= 0.5", "a", id="decimal"),
            pytest.param("steps.a.outputs.ok == TRUE", "a", id="uppercase_true"),
            pytest.param("steps.run-tests.exit_code == 0", "run-tests", id="hyphenated_step_id"),
            pytest.param("iteration.index > 1", "a", id="iteration_index"),
            pytest.param("steps.a.outputs == 'x'", "a", id="outputs_without_key"),
            pytest.param("steps.a.stdout == a/b", "a", id="punctuated_bare_word"),
            pytest.param('steps.a.stdout == "a == b"', "a", id="operator_inside_a_quoted_right_value"),
        ],
    )
    def test_condition_valid_today_reports_no_errors(self, expression: str, step_id: str) -> None:
        assert validate_condition_expression(expression, known_step_ids={step_id}) == []
