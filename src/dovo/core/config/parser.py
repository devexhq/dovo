"""Typed value parser for configuration entries."""

from __future__ import annotations

import json
from typing import Any

BOOLEAN_ALIASES: dict[str, bool] = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
}


def parse_config_value(value: str) -> Any:
    """Parse a string input into its native Python type.

    Evaluates the input string in a strict, deterministic order:
    1. Explicit string preservation (double-quoted input, e.g. ``'"true"'`` or ``'10'``)
    2. Boolean aliases (case-insensitive match against ``true``, ``false``, ``yes``, ``no``)
    3. Numeric strings (attempt ``int``, then ``float``)
    4. JSON collections (arrays starting with ``[`` or objects starting with ``{``)
    5. Fallback string (unmodified ``str``)

    Args:
        value: Raw string input from CLI or caller.

    Returns:
        The native Python equivalent (bool, int, float, list, dict, or str).
    """
    # 1. Explicit String Preservation (FR-5)
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]

    # 2. Boolean Check (FR-1)
    val_lower = value.lower()
    if val_lower in BOOLEAN_ALIASES:
        return BOOLEAN_ALIASES[val_lower]

    # 3. Numeric Parsing - Integer Check (FR-2)
    try:
        return int(value)
    except ValueError:
        pass

    # 4. Numeric Parsing - Float Check (FR-2)
    try:
        return float(value)
    except ValueError:
        pass

    # 5. JSON Collection Check (FR-3)
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 6. Fallback String (FR-4)
    return value
