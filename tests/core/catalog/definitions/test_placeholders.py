"""Contract tests for the authoring-time syntax check of interpolated placeholders."""

from __future__ import annotations

import pytest

from dovo.core.catalog.definitions import StepDefinition
from dovo.core.catalog.definitions.placeholders import placeholder_syntax_errors

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
        ],
    )
    def test_valid_or_unknown_placeholder_passes(self, run: str) -> None:
        assert _errors({"run": run}) == []
