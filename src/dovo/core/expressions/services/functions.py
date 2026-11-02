"""The functions an expression can call, how many arguments each takes and the value each returns.

A function given an ABSENT argument, or an argument of a type it does not take, returns ABSENT. project reads
the keys of a '<array>.*.<key>' projection from each object of an array.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Final

from dovo.core.expressions.models import ABSENT

KNOWN_FUNCTIONS: Final[tuple[str, ...]] = ("contains", "fromJSON", "join", "length", "trim")


def function_arity(name: str) -> tuple[int, int] | None:
    """Return the fewest and most arguments function name takes, or None when no function has that name."""
    function = _FUNCTIONS.get(name)
    return None if function is None else function[0]


def call_function(name: str, arguments: Sequence[object]) -> object:
    """Return the value of known function name on arguments; ABSENT when any argument is ABSENT or of a type it does not take."""
    if any(argument is ABSENT for argument in arguments):
        return ABSENT
    return _FUNCTIONS[name][1](*arguments)


def project(value: object, keys: Sequence[str]) -> object:
    """Return what keys reach in each object of the array value, skipping objects they miss; ABSENT when value is not an array.

    A string value is read as JSON first, so a recorded output holding a JSON array projects too.
    """
    if isinstance(value, str):
        value = _from_json(value)
    if not isinstance(value, list):
        return ABSENT
    reached = (_reach(item, keys) for item in value)
    return [item for item in reached if item is not ABSENT]


def _reach(value: object, keys: Sequence[str]) -> object:
    """Return what keys reach in value through objects, or ABSENT when a key is missing or a value is not an object."""
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return ABSENT
        value = value[key]
    return value


def _contains(haystack: object, needle: object) -> object:
    """Return whether a string holds a string needle, or a list holds an item equal to needle."""
    if isinstance(haystack, str):
        return needle in haystack if isinstance(needle, str) else ABSENT
    if isinstance(haystack, list):
        return needle in haystack
    return ABSENT


def _from_json(text: object) -> object:
    """Return the value a JSON string holds."""
    if not isinstance(text, str):
        return ABSENT
    try:
        return json.loads(text)
    except ValueError:
        return ABSENT


def _join(items: object, separator: object = ",") -> object:
    """Return a list's items, each rendered as a placeholder renders a value, joined by a string separator."""
    if not isinstance(items, list) or not isinstance(separator, str):
        return ABSENT
    return separator.join(_render(item) for item in items)


def _length(value: object) -> object:
    """Return the length of a string, list or object."""
    return len(value) if isinstance(value, (str, list, dict)) else ABSENT


def _trim(text: object) -> object:
    """Return a string without its leading and trailing whitespace."""
    return text.strip() if isinstance(text, str) else ABSENT


def _render(value: object) -> str:
    """Render a value as a placeholder does: a string as is, None empty, a bool `true`/`false`, a list or dict as JSON, anything else through str()."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


# Each function's (fewest, most) arguments and its implementation, keyed by its exact name.
_FUNCTIONS: Final[dict[str, tuple[tuple[int, int], Callable[..., object]]]] = {
    "contains": ((2, 2), _contains),
    "fromJSON": ((1, 1), _from_json),
    "join": ((1, 2), _join),
    "length": ((1, 1), _length),
    "trim": ((1, 1), _trim),
}
