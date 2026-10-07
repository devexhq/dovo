"""Tier 2 presentation contract tests for WorktreeListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_list import WorktreeListFormatter
from dovo.core.db import WorktreeRecord, WorktreeStatus
from dovo.core.worktree.models import (
    WorktreeListResult,
    WorktreeListStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = WorktreeRecord(
    id="dovo_list1234",
    name="list-worktree",
    branch_name="dovo/list",
    base_commit="abc1234",
    worktree_path="/tmp/dovo_list1234",
    status=WorktreeStatus.ACTIVE,
    created_at="2026-08-31T20:00:00Z",
    updated_at="2026-08-31T20:00:00Z",
)

WITH_WORKTREES = FormatterCase(
    data=WorktreeListResult(status=WorktreeListStatus.OK, worktrees=[_RECORD]),
    view=WorktreeListResult(status=WorktreeListStatus.OK, worktrees=[_RECORD]),
    render_expectations=[_RECORD.id, "list-worktree", _RECORD.branch_name],
)

EMPTY_WORKTREES = FormatterCase(
    data=WorktreeListResult(status=WorktreeListStatus.OK, worktrees=[]),
    view=WorktreeListResult(status=WorktreeListStatus.OK, worktrees=[]),
    render_expectations=[],
)

WORKTREE_LIST_CASES = [
    pytest.param(WITH_WORKTREES, id="with_worktrees"),
    pytest.param(EMPTY_WORKTREES, id="empty_worktrees"),
]

WORKTREE_LIST_PAYLOAD_CASES = [
    pytest.param(
        WITH_WORKTREES,
        {
            "status": "ok",
            "worktrees": [
                {
                    "id": "dovo_list1234",
                    "name": "list-worktree",
                    "branch_name": "dovo/list",
                    "base_commit": "abc1234",
                    "worktree_path": "/tmp/dovo_list1234",
                    "status": "active",
                    "created_at": "2026-08-31T20:00:00Z",
                    "updated_at": "2026-08-31T20:00:00Z",
                }
            ],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="with_worktrees",
    ),
    pytest.param(
        EMPTY_WORKTREES,
        {
            "status": "ok",
            "worktrees": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="empty_worktrees",
    ),
]


class WorktreeListFormatterTests:
    """Tier 2 presentation contract tests for WorktreeListFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_LIST_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeListResult, WorktreeListResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WorktreeListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_LIST_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeListResult, WorktreeListResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeListFormatter, case.data, case.render_expectations)
