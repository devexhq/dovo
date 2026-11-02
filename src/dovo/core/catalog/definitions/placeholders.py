"""Authoring-time checks of the `{{ }}` and `${{ }}` placeholders run time interpolates."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from dovo.core.catalog.definitions.conditions import expression_leaves
from dovo.core.expressions import Expression, InvalidExpression, Path, Projection, parse_expression
from dovo.core.inputs import find_placeholders, placeholder_texts

_INPUTS_ROOT = "inputs"


def placeholder_syntax_errors(steps: Sequence[object]) -> list[str]:
    """Return one message per placeholder whose body is not an expression, in step, field and text order."""
    return [error for step in steps for error in _step_errors(step)]


def placeholder_input_names(steps: Sequence[object]) -> set[str]:
    """Return the name of every `inputs.<name>` path read anywhere in a placeholder body of steps that parses."""
    return {name for step in steps for _, text in placeholder_texts(step) for name in _text_input_names(text)}


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


def _parsed_placeholders(text: str) -> Iterator[Expression]:
    """Yield the parsed tree of each placeholder in text whose stripped body parses."""
    for _, _, body in find_placeholders(text):
        expression = parse_expression(body.strip())
        if not isinstance(expression, InvalidExpression):
            yield expression


def _text_input_names(text: str) -> set[str]:
    """Return the first segment of every operand in text's placeholders that is a path rooted at 'inputs' by name.

    A projection's base is the operand it reads.
    """
    operands = (
        leaf.base if isinstance(leaf, Projection) else leaf
        for expression in _parsed_placeholders(text)
        for leaf, _ in expression_leaves(expression)
    )
    return {
        operand.segments[0]
        for operand in operands
        if isinstance(operand, Path)
        and operand.root == _INPUTS_ROOT
        and operand.segments
        and isinstance(operand.segments[0], str)
    }
