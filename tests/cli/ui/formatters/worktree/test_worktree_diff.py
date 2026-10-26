"""Tier 2 presentation contract tests for WorktreeDiffFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_diff import WorktreeDiffFormatter
from dovo.common.models import Remediation
from dovo.core.worktree.models import (
    WorktreeDiffResult,
    WorktreeDiffStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

OK_WITH_DIFF = FormatterCase.identity(
    data=WorktreeDiffResult(
        status=WorktreeDiffStatus.OK,
        worktree_id="dovo_diff",
        diff_text="+new_line",
    ),
    render_expectations=["+new_line"],
)

EMPTY_DIFF = FormatterCase.identity(
    data=WorktreeDiffResult(
        status=WorktreeDiffStatus.EMPTY_DIFF,
        worktree_id="dovo_empty",
    ),
    render_expectations=["dovo_empty"],
)

NOT_FOUND = FormatterCase.identity(
    data=WorktreeDiffResult(
        status=WorktreeDiffStatus.NOT_FOUND,
        worktree_id="dovo_missing",
        errors=["Worktree 'dovo_missing' not found."],
        remediations=[
            Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")
        ],
    ),
    render_expectations=[
        "dovo_missing",
        "Worktree 'dovo_missing' not found.",
        "Fix:",
        "- Run 'dovo worktree list' to see known worktrees",
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
            "remediations": [],
            "diagnostics": [],
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
            "remediations": [],
            "diagnostics": [],
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
            "remediations": [
                {"message": "Run 'dovo worktree list' to see known worktrees", "command": "dovo worktree list"}
            ],
            "diagnostics": [],
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
        assert_json_payload_matches_published_shape(WorktreeDiffFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_DIFF_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeDiffResult, WorktreeDiffResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeDiffFormatter, case.data, case.render_expectations)
