"""Tier 2 presentation contract tests for WorktreeLifecycleFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import WorktreeLifecycleEvent
from dovo.cli.ui.formatters.events.worktree import WorktreeLifecycleFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

READY_ACTIVE = FormatterCase(
    data=WorktreeLifecycleEvent(action="ready", path="/tmp/worktree1", active=True, kept=None),
    view=WorktreeLifecycleEvent(action="ready", path="/tmp/worktree1", active=True, kept=None),
    render_expectations=["/tmp/worktree1"],
)

READY_IN_PLACE = FormatterCase(
    data=WorktreeLifecycleEvent(action="ready", path="", active=False, kept=None),
    view=WorktreeLifecycleEvent(action="ready", path="", active=False, kept=None),
    render_expectations=[],
)

CLEANUP_RETAINED = FormatterCase(
    data=WorktreeLifecycleEvent(action="cleanup", path="/tmp/worktree2", active=None, kept=True),
    view=WorktreeLifecycleEvent(action="cleanup", path="/tmp/worktree2", active=None, kept=True),
    render_expectations=["/tmp/worktree2"],
)

CLEANUP_CLEANED = FormatterCase(
    data=WorktreeLifecycleEvent(action="cleanup", path="", active=None, kept=False),
    view=WorktreeLifecycleEvent(action="cleanup", path="", active=None, kept=False),
    render_expectations=[],
)

WORKTREE_CASES = [
    pytest.param(READY_ACTIVE, id="ready_active"),
    pytest.param(READY_IN_PLACE, id="ready_in_place"),
    pytest.param(CLEANUP_RETAINED, id="cleanup_retained"),
    pytest.param(CLEANUP_CLEANED, id="cleanup_cleaned"),
]

WORKTREE_PAYLOAD_CASES = [
    pytest.param(
        READY_ACTIVE,
        {
            "action": "ready",
            "path": "/tmp/worktree1",
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
            "path": "/tmp/worktree2",
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


class WorktreeLifecycleFormatterTests:
    """Tier 2 presentation contract tests for WorktreeLifecycleFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeLifecycleEvent, WorktreeLifecycleEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WorktreeLifecycleFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeLifecycleEvent, WorktreeLifecycleEvent]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeLifecycleFormatter, case.data, case.render_expectations)
