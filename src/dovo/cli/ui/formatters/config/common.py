"""Shared formatting helpers for configuration formatters."""

from __future__ import annotations

import json


def format_config_value(value: object) -> str:
    """Format parsed value for CLI output."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, list, dict)):
        return json.dumps(value)
    return str(value)
