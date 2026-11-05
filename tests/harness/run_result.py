"""Readers for the JSON event stream of `dovo run` and `dovo resume`."""

from __future__ import annotations

import json
from typing import Final, TypedDict, cast

RUN_RESULT: Final[str] = "run.result"


class RunResultPayload(TypedDict):
    """The published JSON payload of a `run.result` event."""

    ok: bool
    error_code: str | None
    errors: list[str]
    warnings: list[str]
    remediations: list[dict[str, str | None]]
    diagnostics: list[dict[str, str]]
    session_id: str | None
    blueprint_name: str | None
    session_status: str | None
    paused_prompt_id: str | None


JSON_LINE_KEYS: Final[list[str]] = ["version", "event_type", "payload"]


def json_events(stdout: str) -> list[dict[str, object]]:
    """Return every JSON line printed to stdout, in order, asserting each is a version 1 envelope."""
    events: list[dict[str, object]] = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    for event in events:
        assert list(event) == JSON_LINE_KEYS, event
        assert event["version"] == 1, event
    return events


def run_result_payload(stdout: str) -> RunResultPayload:
    """Return the payload of the last stdout line, asserting it is the stream's one `run.result`."""
    events = json_events(stdout)
    assert [event["event_type"] for event in events].count(RUN_RESULT) == 1, stdout
    assert events[-1]["event_type"] == RUN_RESULT, stdout
    return cast(RunResultPayload, events[-1]["payload"])
