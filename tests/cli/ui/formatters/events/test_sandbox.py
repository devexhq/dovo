"""Tier 2 presentation contract tests for SandboxLifecycleFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import SandboxLifecycleEvent
from dovo.cli.ui.formatters.events.sandbox import SandboxLifecycleFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

READY_ACTIVE = FormatterCase(
    data=SandboxLifecycleEvent(action="ready", path="/tmp/sbx1", active=True, kept=None),
    view=SandboxLifecycleEvent(action="ready", path="/tmp/sbx1", active=True, kept=None),
    render_expectations=["/tmp/sbx1"],
)

READY_IN_PLACE = FormatterCase(
    data=SandboxLifecycleEvent(action="ready", path="", active=False, kept=None),
    view=SandboxLifecycleEvent(action="ready", path="", active=False, kept=None),
    render_expectations=[],
)

CLEANUP_RETAINED = FormatterCase(
    data=SandboxLifecycleEvent(action="cleanup", path="/tmp/sbx2", active=None, kept=True),
    view=SandboxLifecycleEvent(action="cleanup", path="/tmp/sbx2", active=None, kept=True),
    render_expectations=["/tmp/sbx2"],
)

CLEANUP_CLEANED = FormatterCase(
    data=SandboxLifecycleEvent(action="cleanup", path="", active=None, kept=False),
    view=SandboxLifecycleEvent(action="cleanup", path="", active=None, kept=False),
    render_expectations=[],
)

SANDBOX_CASES = [
    pytest.param(READY_ACTIVE, id="ready_active"),
    pytest.param(READY_IN_PLACE, id="ready_in_place"),
    pytest.param(CLEANUP_RETAINED, id="cleanup_retained"),
    pytest.param(CLEANUP_CLEANED, id="cleanup_cleaned"),
]

SANDBOX_PAYLOAD_CASES = [
    pytest.param(
        READY_ACTIVE,
        {
            "action": "ready",
            "path": "/tmp/sbx1",
            "active": True,
            "kept": None,
        },
        id="ready_active",
    ),
    pytest.param(
        READY_IN_PLACE,
        {
            "action": "ready",
            "path": "",
            "active": False,
            "kept": None,
        },
        id="ready_in_place",
    ),
    pytest.param(
        CLEANUP_RETAINED,
        {
            "action": "cleanup",
            "path": "/tmp/sbx2",
            "active": None,
            "kept": True,
        },
        id="cleanup_retained",
    ),
    pytest.param(
        CLEANUP_CLEANED,
        {
            "action": "cleanup",
            "path": "",
            "active": None,
            "kept": False,
        },
        id="cleanup_cleaned",
    ),
]


class SandboxLifecycleFormatterTests:
    """Tier 2 presentation contract tests for SandboxLifecycleFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), SANDBOX_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[SandboxLifecycleEvent, SandboxLifecycleEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(SandboxLifecycleFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SANDBOX_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[SandboxLifecycleEvent, SandboxLifecycleEvent]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(SandboxLifecycleFormatter, case.data, case.render_expectations)
