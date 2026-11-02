"""Resolution of `steps.<id>.outputs.<key>`, shared by step conditions and placeholders."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

from dovo.core.inputs.models import StepOutputValue

_UNPARSABLE: Final = object()
"""Marks a value that is not valid JSON, as opposed to a JSON null."""


def _parse_json(text: str) -> object:
    """Parse text as JSON, or return _UNPARSABLE when it is not valid JSON."""
    try:
        return json.loads(text)
    except ValueError:
        return _UNPARSABLE


def _follow_path(root: object, path: Sequence[str | int]) -> StepOutputValue | None:
    """Follow path through nested JSON objects from root; None when a segment is missing, crosses a non-object or is an int."""
    current = root
    for segment in path:
        if isinstance(segment, int) or not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return StepOutputValue(current)


def read_json_path(text: str, path: Sequence[str | int]) -> StepOutputValue | None:
    """Parse text as JSON and follow path through its objects; None when text is not JSON or path misses, and an int segment always misses."""
    parsed = _parse_json(text)
    return None if parsed is _UNPARSABLE else _follow_path(parsed, path)


def resolve_step_output(
    outputs: Mapping[str, str], stdout: str, key: str, path: Sequence[str | int] = ()
) -> StepOutputValue | None:
    """Return the recorded output for key, else key of the stdout JSON object, following path through its parsed JSON; None when not found.

    path matches object keys only, so an int segment, an unquoted bracket index, finds nothing.
    """
    if key in outputs:
        recorded = outputs[key]
        return read_json_path(recorded, path) if path else StepOutputValue(recorded)
    parsed = _parse_json(stdout)
    if not isinstance(parsed, dict) or key not in parsed:
        return None
    value = parsed[key]
    if path and isinstance(value, str):
        value = _parse_json(value)
    return _follow_path(value, path)


def render_step_output(value: object) -> str:
    """Render a resolved value as recorded outputs hold it: a string as is, None empty, a bool `true`/`false`, a list or dict as JSON, anything else through str()."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
