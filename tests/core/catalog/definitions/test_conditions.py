from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import validate_condition_expression
from dovo.core.catalog.definitions.conditions import ConditionScope

_PUBLISHED = frozenset({"verdict", "findings", "team.plan"})
_UNTIL_SCOPE = ConditionScope(known_step_ids=frozenset({"a"}), published_artifacts=_PUBLISHED)
_WHEN_SCOPE = ConditionScope(
    clause="when",
    known_step_ids=frozenset({"a"}),
    scope_label="earlier steps",
    allow_iteration=False,
    published_artifacts=_PUBLISHED,
)


def _until_scope(*step_ids: str, published: frozenset[str] = _PUBLISHED) -> ConditionScope:
    """Return an until scope over step_ids that reads the published artifact names."""
    return ConditionScope(known_step_ids=frozenset(step_ids), published_artifacts=published)


class ConditionExpressionValidatorTests:
    """Unit tests verifying semantic validation of until condition expressions."""

    def test_validate_condition_expression_detects_unknown_step_ids(self) -> None:
        expression = "steps.unknown_step.exit_code == 0"
        errors = validate_condition_expression(expression, scope=_until_scope("build"))
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
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [message]

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            pytest.param(
                "not a condition",
                "Invalid expression 'not a condition': unquoted text 'not a condition' at column 1; "
                "quote a value of more than one word.",
                id="bare_words",
            ),
            pytest.param(
                "done!",
                "Condition 'done!' must reference at least one dynamic operand "
                "('steps.<id>.<field>' or 'iteration.index').",
                id="punctuated_word",
            ),
            pytest.param('"abc', "Invalid expression '\"abc': unterminated string at column 1.", id="tokenize_error"),
            pytest.param(
                "1e3", "Invalid expression '1e3': unsupported number '1e3' at column 1.", id="unsupported_number"
            ),
        ],
    )
    def test_entry_without_an_operator_reports_its_grammar_error(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [message]

    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param('steps.a.status == "completed" || steps.a.exit_code == 0', id="or"),
            pytest.param("steps.a.exit_code == 0 && iteration.index > 1", id="and_with_iteration"),
            pytest.param("!(steps.a.exit_code == 0)", id="not_of_a_group"),
            pytest.param("!steps.a.stdout", id="not_of_a_path"),
            pytest.param('steps.a.outputs.s in ["x", 1, true]', id="in_list"),
            pytest.param('steps.a.outputs.s not in ["x"]', id="not_in_list"),
            pytest.param("steps.a.status in [completed, skipped]", id="bare_word_items"),
            pytest.param("steps.a.exit_code", id="lone_path"),
            pytest.param("(steps.a.exit_code == 0)", id="parenthesised"),
            pytest.param("length(fromJSON(steps.a.stdout)) > 2", id="compared_call"),
            pytest.param("contains(steps.a.stdout, 'x')", id="call"),
            pytest.param("contains(steps.a.outputs.x.*.y, 'v')", id="projection"),
        ],
    )
    def test_compound_and_bare_value_entries_report_no_errors(self, expression: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == []

    @pytest.mark.parametrize(
        ("expression", "item"),
        [
            pytest.param("steps.a.status in [steps.a.stdout]", "steps.a.stdout", id="step_path"),
            pytest.param("steps.a.status in [v1.2]", "v1.2", id="dotted_word"),
            pytest.param("steps.a.status in [iteration]", "iteration", id="iteration"),
        ],
    )
    def test_list_item_that_is_not_a_literal_is_rejected(self, expression: str, item: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [
            f"List item '{item}' in until condition '{expression}' is not a literal. "
            "Items after 'in' and 'not in' are quoted strings, numbers, booleans or single words."
        ]

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            pytest.param(
                "steps.a.exit_code == 0 && steps.zzz.exit_code == 0",
                "Step id 'zzz' referenced in until condition 'steps.a.exit_code == 0 && steps.zzz.exit_code == 0' "
                "not found in loop 'do' steps (allowed: a).",
                id="unknown_step_in_the_right_arm",
            ),
            pytest.param(
                '!(inputs.mode == "x")',
                "Operand 'inputs.mode' in until condition '!(inputs.mode == \"x\")' reads 'inputs', "
                "which a condition cannot read. Readable roots: artifacts, iteration, steps.",
                id="unreadable_root_under_not",
            ),
        ],
    )
    def test_every_operand_of_a_compound_entry_is_checked(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [message]

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            pytest.param(
                "contains(steps.zz.outputs.x.*.y, 'v')",
                "Step id 'zz' referenced in until condition 'contains(steps.zz.outputs.x.*.y, 'v')' "
                "not found in loop 'do' steps (allowed: a).",
                id="unknown_step",
            ),
            pytest.param(
                "steps.a.result.*.y",
                "Unknown step field 'result' in 'steps.a.result'. Allowed fields: error_code, exit_code, outputs, status, stdout.",
                id="unknown_field",
            ),
            pytest.param(
                "steps.a.stdout == x.*.y",
                "Operand 'x.*.y' in until condition 'steps.a.stdout == x.*.y' projects 'x', "
                "which is not a step or iteration value.",
                id="one_word_base",
            ),
        ],
    )
    def test_projection_base_is_checked_as_an_operand(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [message]

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
        assert validate_condition_expression(expression, scope=_until_scope("a", "contains")) == []

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
            pytest.param(
                '"x" == "x" || true',
                'Condition \'"x" == "x" || true\' must reference at least one dynamic operand '
                "('steps.<id>.<field>' or 'iteration.index').",
                id="constant_compound",
            ),
        ],
    )
    def test_existing_operand_message_is_unchanged(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [message]

    @pytest.mark.parametrize(
        ("expression", "scope", "message"),
        [
            pytest.param(
                "steps.a.status == inputs.mode",
                _UNTIL_SCOPE,
                "Operand 'inputs.mode' in until condition 'steps.a.status == inputs.mode' reads 'inputs', "
                "which a condition cannot read. Readable roots: artifacts, iteration, steps.",
                id="inputs_in_until",
            ),
            pytest.param(
                "steps.a.status == inputs.mode",
                _WHEN_SCOPE,
                "Operand 'inputs.mode' in when condition 'steps.a.status == inputs.mode' reads 'inputs', "
                "which a condition cannot read. Readable roots: artifacts, steps.",
                id="inputs_in_when",
            ),
            pytest.param(
                "steps.a.stdout == a.py",
                _UNTIL_SCOPE,
                "Operand 'a.py' in until condition 'steps.a.stdout == a.py' reads 'a', "
                "which a condition cannot read. Readable roots: artifacts, iteration, steps.",
                id="dotted_bare_value",
            ),
            pytest.param(
                'inputs.mode == "x"',
                _UNTIL_SCOPE,
                "Operand 'inputs.mode' in until condition 'inputs.mode == \"x\"' reads 'inputs', "
                "which a condition cannot read. Readable roots: artifacts, iteration, steps.",
                id="inputs_only",
            ),
        ],
    )
    def test_unreadable_root_is_rejected(self, expression: str, scope: ConditionScope, message: str) -> None:
        assert validate_condition_expression(expression, scope=scope) == [message]

    @pytest.mark.parametrize(
        ("expression", "step_id"),
        [
            pytest.param('steps.fix.outputs.status != "no_op"', "fix", id="quoted_status"),
            pytest.param('steps.fix.error_code == ""', "fix", id="error_code"),
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
        assert validate_condition_expression(expression, scope=_until_scope(step_id)) == []

    @pytest.mark.parametrize(
        ("expression", "operand"),
        [
            pytest.param('steps[0].status == "x"', "steps[0].status", id="step_by_position"),
            pytest.param('steps.a.outputs.files[0] == "x"', "steps.a.outputs.files[0]", id="index_in_an_output_path"),
            pytest.param("x[1] == steps.a.stdout", "x[1]", id="index_on_a_word"),
            pytest.param(
                'artifacts.plan.content[0] == "x"', "artifacts.plan.content[0]", id="index_in_artifact_content"
            ),
        ],
    )
    def test_bracket_index_is_rejected(self, expression: str, operand: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [
            f"Operand '{operand}' in until condition '{expression}' holds a bracket index, which a condition cannot read."
        ]

    @pytest.mark.parametrize(
        ("expression", "reason"),
        [
            pytest.param("lenght(steps.a.stdout) > 0", "unknown function 'lenght' at column 1", id="unknown"),
            pytest.param(
                "contains(steps.a.stdout)", "function 'contains' at column 1 takes 2 arguments, not 1", id="too_few"
            ),
            pytest.param(
                "trim(steps.a.stdout, 'x')", "function 'trim' at column 1 takes 1 argument, not 2", id="too_many"
            ),
        ],
    )
    def test_call_error_is_a_load_error(self, expression: str, reason: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [
            f"Invalid expression '{expression}': {reason}; known functions: contains, fromJSON, join, length, trim."
        ]

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            pytest.param(
                "contains(steps.zz.stdout, 'x')",
                "Step id 'zz' referenced in until condition 'contains(steps.zz.stdout, 'x')' "
                "not found in loop 'do' steps (allowed: a).",
                id="unknown_step",
            ),
            pytest.param(
                "length(inputs.x) > 0",
                "Operand 'inputs.x' in until condition 'length(inputs.x) > 0' reads 'inputs', "
                "which a condition cannot read. Readable roots: artifacts, iteration, steps.",
                id="unreadable_root",
            ),
            pytest.param(
                "contains('ab', 'b')",
                "Condition 'contains('ab', 'b')' must reference at least one dynamic operand "
                "('steps.<id>.<field>' or 'iteration.index').",
                id="no_dynamic_operand",
            ),
        ],
    )
    def test_call_arguments_are_checked_as_operands(self, expression: str, message: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [message]

    @pytest.mark.parametrize("scope", [pytest.param(_UNTIL_SCOPE, id="until"), pytest.param(_WHEN_SCOPE, id="when")])
    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param('artifacts.verdict.content == "x"', id="content"),
            pytest.param("artifacts.verdict.content.done", id="lone_content_path"),
            pytest.param("length(fromJSON(artifacts.findings.content)) > 0", id="call_argument"),
            pytest.param("contains(artifacts.findings.content.*.severity, 'high')", id="projection"),
            pytest.param("artifacts['team.plan'].revision >= 2", id="bracket_key_name"),
        ],
    )
    def test_artifact_operand_reports_no_errors(self, expression: str, scope: ConditionScope) -> None:
        assert validate_condition_expression(expression, scope=scope) == []

    @pytest.mark.parametrize(
        ("expression", "operand"),
        [
            pytest.param('artifacts.plan == "x"', "artifacts.plan", id="name_only"),
            pytest.param('artifacts.plan.bogus == "x"', "artifacts.plan.bogus", id="unknown_field"),
        ],
    )
    def test_artifact_operand_without_a_field_is_rejected(self, expression: str, operand: str) -> None:
        assert validate_condition_expression(expression, scope=_UNTIL_SCOPE) == [
            f"Operand '{operand}' in until condition '{expression}' reads no artifact field. "
            "Expected 'artifacts.<name>.<field>' with a field of content, digest, dir, revision."
        ]

    @pytest.mark.parametrize(
        ("published", "listed"),
        [pytest.param(frozenset({"plan"}), "plan", id="plan_published"), pytest.param(frozenset(), "none", id="none")],
    )
    @pytest.mark.parametrize(
        "expression",
        [
            pytest.param('artifacts.ghost.content == "x"', id="comparison"),
            pytest.param("length(artifacts.ghost.content) > 0", id="call_argument"),
            pytest.param("contains(artifacts.ghost.content.*.k, 'x')", id="projection"),
        ],
    )
    def test_artifact_no_step_publishes_is_rejected(
        self, expression: str, published: frozenset[str], listed: str
    ) -> None:
        scope = _until_scope("a", published=published)

        assert validate_condition_expression(expression, scope=scope) == [
            f"Artifact 'ghost' read in until condition '{expression}' is not published by "
            f"any step's 'artifacts:' in the blueprint (published: {listed})."
        ]
