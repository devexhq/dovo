"""Authoring-time syntax check of the `{{ }}` and `${{ }}` placeholders run time interpolates."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.core.expressions import InvalidExpression, parse_expression
from dovo.core.inputs import find_placeholders, placeholder_texts


def placeholder_syntax_errors(steps: Sequence[object]) -> list[str]:
    """Return one message per placeholder whose body is not an expression, in step, field and text order."""
    return [error for step in steps for error in _step_errors(step)]


def _step_errors(step: object) -> list[str]:
    """Return one message per unparsable placeholder in the fields of step, prefixed with its id and field."""
    step_id = getattr(step, "id", "")
    return [
        f"Step '{step_id}', {field_name}: {message}"
        for field_name, text in placeholder_texts(step)
        for message in _text_errors(text)
    ]


def _text_errors(text: str) -> list[str]:
    """Return the InvalidExpression message of each placeholder in text whose stripped body does not parse."""
    messages: list[str] = []
    for _, _, body in find_placeholders(text):
        expression = parse_expression(body.strip())
        if isinstance(expression, InvalidExpression):
            messages.append(expression.message)
    return messages
