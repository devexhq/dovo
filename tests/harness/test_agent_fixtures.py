"""Leak scan and coverage checks for the committed provider fixtures."""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

from tests.harness.fixtures import AGENT_FIXTURE_DIR, load_agent_fixture

_FIXTURE_PATHS = sorted(AGENT_FIXTURE_DIR.rglob("*.jsonl"))
_LEAK_PATTERNS = {
    "api key": re.compile(r"sk-ant-"),
    "home directory": re.compile(r"(?<!\w)(?:/home/|/Users/)[A-Za-z0-9_.-]+|C:\\Users\\"),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "session uuid": re.compile(
        r"\b(?!00000000-0000-4000-8000-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    ),
}
_CLAUDE_SCENARIOS = [
    "planning",
    "reviewing",
    "implementing_create",
    "implementing_edit",
    "permission_denied",
    "permission_denied_by_rule",
    "tool_failure",
    "provider_error",
    "unmapped_tool",
]


class TestFixtureScrubbing:
    """Committed fixtures never contain secrets or machine-identifying data."""

    def test_fixtures_exist(self) -> None:
        assert _FIXTURE_PATHS

    @pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=lambda path: f"{path.parent.name}/{path.stem}")
    def test_fixture_matches_no_leak_pattern(self, fixture_path: Path) -> None:
        content = fixture_path.read_text(encoding="utf-8")

        leaks = [label for label, pattern in _LEAK_PATTERNS.items() if pattern.search(content)]

        assert leaks == []

    @pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=lambda path: f"{path.parent.name}/{path.stem}")
    def test_fixture_does_not_contain_hostname(self, fixture_path: Path) -> None:
        hostname = socket.gethostname()

        assert len(hostname) < 4 or hostname not in fixture_path.read_text(encoding="utf-8")


class TestClaudeScenarioCoverage:
    """Each Claude scenario has one fixture ending in a terminal result, except the truncated stream."""

    @pytest.mark.parametrize("scenario", _CLAUDE_SCENARIOS)
    def test_scenario_ends_with_result_event(self, scenario: str) -> None:
        last_event = json.loads(load_agent_fixture("claude", scenario)[-1])

        assert last_event["type"] == "result"

    def test_truncated_stream_has_no_result_event(self) -> None:
        events = [json.loads(line) for line in load_agent_fixture("claude", "truncated_no_result")]

        assert all(event["type"] != "result" for event in events)
