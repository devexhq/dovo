"""Tier 2 presentation contract tests for WorktreeLifecycleFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import WorktreeLifecycleEvent
from dovo.cli.ui.formatters.events.worktree import WorktreeLifecycleFormatter
from dovo.common.models import Remediation
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

READY_ACTIVE = FormatterCase.identity(
    data=WorktreeLifecycleEvent(action="ready", path="/tmp/worktree1", active=True, kept=None),
    render_expectations=["/tmp/worktree1"],
)

READY_IN_PLACE = FormatterCase.identity(
    data=WorktreeLifecycleEvent(action="ready", path="", active=False, kept=None),
    render_expectations=["Worktree: In-place (workspace)"],
)

CLEANUP_RETAINED = FormatterCase.identity(
    data=WorktreeLifecycleEvent(action="cleanup", path="/tmp/worktree2", active=None, kept=True),
    render_expectations=["/tmp/worktree2"],
)

CLEANUP_CLEANED = FormatterCase.identity(
    data=WorktreeLifecycleEvent(action="cleanup", path="", active=None, kept=False),
    render_expectations=["Worktree: Cleaned"],
)

CLEANUP_KEPT_WITH_FIX = FormatterCase.identity(
    data=WorktreeLifecycleEvent(
        action="cleanup",
        path=".dovo/worktrees/s1",
        kept=True,
        worktree_id="s1",
        branch="dovo/s1",
        remediations=[
            Remediation(
                message="Inspect it with 'git -C .dovo/worktrees/s1 status'", command="git -C .dovo/worktrees/s1 status"
            ),
            Remediation(message="Remove it with 'dovo worktree delete s1'", command="dovo worktree delete s1"),
        ],
    ),
    render_expectations=[
        "Worktree: Kept at .dovo/worktrees/s1 (branch dovo/s1)",
        "Fix:",
        "- Inspect it with 'git -C .dovo/worktrees/s1 status'",
        "- Remove it with 'dovo worktree delete s1'",
    ],
)

WORKTREE_CASES = [
    pytest.param(READY_ACTIVE, id="ready_active"),
    pytest.param(READY_IN_PLACE, id="ready_in_place"),
    pytest.param(CLEANUP_RETAINED, id="cleanup_retained"),
    pytest.param(CLEANUP_CLEANED, id="cleanup_cleaned"),
    pytest.param(CLEANUP_KEPT_WITH_FIX, id="cleanup_kept_with_fix"),
]

WORKTREE_PAYLOAD_CASES = [
    pytest.param(
        READY_IN_PLACE,
        {
            "action": "ready",
            "path": "",
            "active": False,
            "kept": None,
            "worktree_id": None,
            "branch": None,
            "remediations": [],
        },
        id="ready_in_place",
    ),
    pytest.param(
        CLEANUP_KEPT_WITH_FIX,
        {
            "action": "cleanup",
            "path": ".dovo/worktrees/s1",
            "active": None,
            "kept": True,
            "worktree_id": "s1",
            "branch": "dovo/s1",
            "remediations": [
                {
                    "message": "Inspect it with 'git -C .dovo/worktrees/s1 status'",
                    "command": "git -C .dovo/worktrees/s1 status",
                },
                {"message": "Remove it with 'dovo worktree delete s1'", "command": "dovo worktree delete s1"},
            ],
        },
        id="cleanup_kept_with_fix",
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
        assert_json_payload_matches_published_shape(WorktreeLifecycleFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeLifecycleEvent, WorktreeLifecycleEvent]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeLifecycleFormatter, case.data, case.render_expectations)

    def test_cleanup_kept_without_id_and_branch_renders_retained_without_fix(self) -> None:
        event = WorktreeLifecycleEvent(action="cleanup", path="/tmp/worktree2", kept=True)

        rendered = WorktreeLifecycleFormatter().to_rich(event)

        assert rendered.plain == "Worktree: Retained (/tmp/worktree2)"
