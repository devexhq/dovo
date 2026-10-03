"""Tier 2 presentation contract tests for WorktreeDiffFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_diff import WorktreeDiffFormatter
from dovo.core.worktree.models import (
    WorktreeDiffResult,
    WorktreeDiffStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

OK_WITH_DIFF = FormatterCase(
    data=WorktreeDiffResult(
        status=WorktreeDiffStatus.OK,
        worktree_id="dovo_diff",
        diff_text="+new_line",
    ),
    view=WorktreeDiffResult(
        status=WorktreeDiffStatus.OK,
        worktree_id="dovo_diff",
        diff_text="+new_line",
    ),
    render_expectations=["+new_line"],
)

EMPTY_DIFF = FormatterCase(
    data=WorktreeDiffResult(
        status=WorktreeDiffStatus.EMPTY_DIFF,
        worktree_id="dovo_empty",
    ),
    view=WorktreeDiffResult(
        status=WorktreeDiffStatus.EMPTY_DIFF,
        worktree_id="dovo_empty",
    ),
    render_expectations=["dovo_empty"],
)

NOT_FOUND = FormatterCase(
    data=WorktreeDiffResult(
        status=WorktreeDiffStatus.NOT_FOUND,
        worktree_id="dovo_missing",
        errors=["Worktree 'dovo_missing' not found."],
        fixes=["Run `dovo worktree list` to see known worktrees"],
    ),
    view=WorktreeDiffResult(
        status=WorktreeDiffStatus.NOT_FOUND,
        worktree_id="dovo_missing",
        errors=["Worktree 'dovo_missing' not found."],
        fixes=["Run `dovo worktree list` to see known worktrees"],
    ),
    render_expectations=[
        "dovo_missing",
        "Worktree 'dovo_missing' not found.",
        "Run `dovo worktree list` to see known worktrees",
    ],
)

WORKTREE_DIFF_CASES = [
    pytest.param(OK_WITH_DIFF, id="ok_with_diff"),
    pytest.param(EMPTY_DIFF, id="empty_diff"),
    pytest.param(NOT_FOUND, id="not_found"),
]

WORKTREE_DIFF_PAYLOAD_CASES = [
    pytest.param(
        OK_WITH_DIFF,
        {
            "status": "ok",
            "worktree_id": "dovo_diff",
            "diff_text": "+new_line",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="ok_with_diff",
    ),
    pytest.param(
        EMPTY_DIFF,
        {
            "status": "empty_diff",
            "worktree_id": "dovo_empty",
            "diff_text": "",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="empty_diff",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "worktree_id": "dovo_missing",
            "diff_text": "",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": ["Worktree 'dovo_missing' not found."],
            "error_code": None,
            "fixes": ["Run `dovo worktree list` to see known worktrees"],
        },
        id="not_found",
    ),
]


class WorktreeDiffFormatterTests:
    """Tier 2 presentation contract tests for WorktreeDiffFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_DIFF_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeDiffResult, WorktreeDiffResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WorktreeDiffFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_DIFF_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeDiffResult, WorktreeDiffResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeDiffFormatter, case.data, case.render_expectations)
