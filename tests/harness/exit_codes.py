"""Assertions pinning the exit a CLI command ends with against the error_code its result published."""

from __future__ import annotations

import json
from typing import Final

from typer.testing import Result

OUTPUT_FORMATS: Final[tuple[str, ...]] = ("terminal", "json")


def assert_exit_follows_result(result: Result, output_format: str, exit_code: int, error_code: str | None) -> None:
    """Assert result exited with exit_code and, under --format json, that its last event's payload carries error_code."""
    assert result.exit_code == exit_code, result.output
    if output_format == "json":
        last_event = json.loads(result.stdout.strip().splitlines()[-1])
        assert last_event["payload"]["error_code"] == error_code, result.stdout
