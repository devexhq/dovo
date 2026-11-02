"""Contract tests for the authoring-time syntax check of interpolated placeholders."""

from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import StepDefinition
from dovo.core.catalog.definitions.placeholders import placeholder_input_names, placeholder_syntax_errors

_UNQUOTED_A_B = "Invalid expression 'a b': unquoted text 'a b' at column 1; quote a value of more than one word."


def _errors(fields: dict[str, object]) -> list[str]:
    """Return the placeholder syntax errors of a step 's' built from fields."""
    return placeholder_syntax_errors([StepDefinition.model_validate({"id": "s", **fields})])


class PlaceholderSyntaxErrorsTests:
    @pytest.mark.parametrize(
        ("fields", "field_name"),
        [
            pytest.param({"type": "command", "command": "{{ a b }}"}, "command", id="command"),
            pytest.param({"type": "agent", "prompt": "{{ a b }}"}, "prompt", id="prompt"),
            pytest.param({"type": "script", "script_path": "{{ a b }}.sh"}, "script_path", id="script_path"),
            pytest.param({"run": "{{ a b }}"}, "run", id="run"),
            pytest.param({"run": "true", "env": {"V": "{{ a b }}"}}, "env.V", id="env_value"),
            pytest.param(
                {"type": "ask", "interaction": {"mode": "text", "message": "{{ a b }}"}},
                "interaction.message",
                id="ask_message",
            ),
        ],
    )
    def test_unparsable_placeholder_names_its_step_and_field(self, fields: dict[str, object], field_name: str) -> None:
        assert _errors(fields) == [f"Step 's', {field_name}: {_UNQUOTED_A_B}"]

    @pytest.mark.parametrize(
        ("run", "expected"),
        [
            pytest.param(
                "echo {{ a b }} ${{ x == }}",
                [
                    f"Step 's', run: {_UNQUOTED_A_B}",
                    "Step 's', run: Invalid expression 'x ==': missing value after '=='.",
                ],
                id="both_syntaxes",
            ),
            pytest.param("echo {{ }}", ["Step 's', run: Invalid expression '': empty expression."], id="empty_body"),
            pytest.param(
                "echo {{ it's }}",
                ["Step 's', run: Invalid expression 'it's': unterminated string at column 3."],
                id="unterminated_quote",
            ),
        ],
    )
    def test_each_placeholder_of_a_text_is_reported(self, run: str, expected: list[str]) -> None:
        assert _errors({"run": run}) == expected

    @pytest.mark.parametrize(
        ("run", "body", "reason"),
        [
            pytest.param(
                "echo {{ lenght(inputs.x) }}", "lenght(inputs.x)", "unknown function 'lenght' at column 1", id="unknown"
            ),
            pytest.param(
                "echo {{ contains(inputs.x) }}",
                "contains(inputs.x)",
                "function 'contains' at column 1 takes 2 arguments, not 1",
                id="too_few",
            ),
            pytest.param(
                "echo ${{ trim(inputs.x, 'y') }}",
                "trim(inputs.x, 'y')",
                "function 'trim' at column 1 takes 1 argument, not 2",
                id="too_many",
            ),
        ],
    )
    def test_call_error_names_step_field_and_known_functions(self, run: str, body: str, reason: str) -> None:
        assert _errors({"run": run}) == [
            f"Step 's', run: Invalid expression '{body}': {reason}; "
            "known functions: contains, fromJSON, join, length, trim."
        ]

    @pytest.mark.parametrize(
        "run",
        [
            pytest.param("echo {{ inputs.x }}", id="input"),
            pytest.param("echo {{ inputs.x || 'y' }}", id="or_default"),
            pytest.param("echo {{ github.sha }}", id="unknown_namespace"),
            pytest.param("echo {{ steps[0].id }}", id="step_position"),
            pytest.param("echo {{ .Values.image }}", id="lone_word"),
            pytest.param("echo ${{ github.ref == 'main' }}", id="other_template_compound"),
            pytest.param("echo {{", id="unclosed_braces"),
            pytest.param("echo hi", id="no_placeholder"),
            pytest.param("echo {{ join(inputs.x, ' ') }}", id="call"),
        ],
    )
    def test_valid_or_unknown_placeholder_passes(self, run: str) -> None:
        assert _errors({"run": run}) == []


def _names(*steps: dict[str, object]) -> set[str]:
    """Return the input names the placeholders of steps built from each fields mapping read."""
    return placeholder_input_names([StepDefinition.model_validate(fields) for fields in steps])


class PlaceholderInputNamesTests:
    @pytest.mark.parametrize(
        ("steps", "expected"),
        [
            pytest.param([{"id": "s", "run": "echo ${{ inputs.a }}"}], {"a"}, id="dollar_lone"),
            pytest.param([{"id": "s", "run": "echo {{ inputs.a }}"}], {"a"}, id="plain_lone"),
            pytest.param([{"id": "s", "run": "echo {{ inputs.a || 'y' }}"}], {"a"}, id="or_default"),
            pytest.param([{"id": "s", "run": "echo {{ inputs.a == inputs.b }}"}], {"a", "b"}, id="comparison"),
            pytest.param([{"id": "s", "run": "echo {{ !inputs.a }}"}], {"a"}, id="negation"),
            pytest.param([{"id": "s", "run": "echo {{ 'x' in [inputs.a] }}"}], {"a"}, id="list_item"),
            pytest.param([{"id": "s", "run": "echo {{ inputs['a.b'] }}"}], {"a.b"}, id="bracket_key"),
            pytest.param([{"id": "s", "run": "echo {{ join(inputs.a, inputs.b) }}"}], {"a", "b"}, id="call_argument"),
            pytest.param([{"id": "s", "run": "echo {{ join(inputs.a.*.name) }}"}], {"a"}, id="projection_base"),
            pytest.param(
                [
                    {"id": "s", "run": "true", "env": {"V": "{{ inputs.a }}"}},
                    {"id": "t", "type": "ask", "interaction": {"mode": "text", "message": "{{ inputs.b }}"}},
                ],
                {"a", "b"},
                id="env_and_message",
            ),
        ],
    )
    def test_reads_every_input_path_in_an_expression(self, steps: list[dict[str, object]], expected: set[str]) -> None:
        assert _names(*steps) == expected

    @pytest.mark.parametrize(
        "run",
        [
            pytest.param("echo {{ a }}", id="bare_name"),
            pytest.param("echo {{ github.sha }}", id="other_root"),
            pytest.param("echo {{ steps.a.outputs.x }}", id="step_output"),
            pytest.param("echo {{ inputs[0] }}", id="index_segment"),
            pytest.param("echo {{ inputs.a b }}", id="unparsable"),
        ],
    )
    def test_reads_no_name_from_other_paths(self, run: str) -> None:
        assert _names({"id": "s", "run": run}) == set()
