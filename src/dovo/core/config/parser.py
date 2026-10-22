"""Typed value parser for configuration entries."""

from __future__ import annotations

import json

from pydantic import JsonValue

BOOLEAN_ALIASES: dict[str, bool] = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
}


def parse_config_value(value: str) -> JsonValue:
    """Coerce a raw ``dovo config set`` token into its native type; the first matching rule wins.

    1. Double-quoted (``len >= 2``, starts and ends with ``"``): the text between the quotes, as ``str``,
       with no further coercion (``'"12345"'`` → ``"12345"``)
    2. Boolean aliases, case-insensitive: ``true``/``yes`` → ``True``, ``false``/``no`` → ``False``
    3. ``int(value)`` succeeds → ``int``
    4. ``float(value)`` succeeds → ``float``
    5. Stripped value starts with ``[`` or ``{`` and ``json.loads`` returns a list or dict → that collection
    6. Otherwise the unchanged ``str``

    Args:
        value: Raw token from the command line.

    Returns:
        The native Python equivalent (bool, int, float, list, dict, or str).
    """
    # 1. Double-quoted string preservation
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]

    # 2. Boolean aliases
    val_lower = value.lower()
    if val_lower in BOOLEAN_ALIASES:
        return BOOLEAN_ALIASES[val_lower]

    # 3. Integer
    try:
        return int(value)
    except ValueError:
        pass

    # 4. Float
    try:
        return float(value)
    except ValueError:
        pass

    # 5. JSON array or object
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 6. Fallback string
    return value
