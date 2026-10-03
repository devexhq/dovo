"""Tier 2 presentation contract tests for WorktreeDeleteFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_delete import WorktreeDeleteFormatter
from dovo.core.worktree.models import (
    WorktreeDeleteResult,
    WorktreeDeleteStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

DELETED = FormatterCase(
    data=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.DELETED,
        worktree_id="dovo_del",
        deleted=True,
    ),
    view=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.DELETED,
        worktree_id="dovo_del",
        deleted=True,
    ),
    render_expectations=["dovo_del"],
)

ALREADY_CLEANED = FormatterCase(
    data=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.ALREADY_CLEANED,
        worktree_id="dovo_del",
        deleted=False,
    ),
    view=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.ALREADY_CLEANED,
        worktree_id="dovo_del",
        deleted=False,
    ),
    render_expectations=["dovo_del"],
)

ABORTED = FormatterCase(
    data=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.ABORTED,
        worktree_id="dovo_del",
        deleted=False,
    ),
    view=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.ABORTED,
        worktree_id="dovo_del",
        deleted=False,
    ),
    render_expectations=["Aborted"],
)

NOT_FOUND = FormatterCase(
    data=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.NOT_FOUND,
        worktree_id="dovo_missing",
        deleted=False,
        errors=["Worktree 'dovo_missing' not found."],
        fixes=["Run `dovo worktree list` to see known worktrees"],
    ),
    view=WorktreeDeleteResult(
        status=WorktreeDeleteStatus.NOT_FOUND,
        worktree_id="dovo_missing",
        deleted=False,
        errors=["Worktree 'dovo_missing' not found."],
        fixes=["Run `dovo worktree list` to see known worktrees"],
    ),
    render_expectations=[
        "dovo_missing",
        "Worktree 'dovo_missing' not found.",
        "Run `dovo worktree list` to see known worktrees",
    ],
)

WORKTREE_DELETE_CASES = [
    pytest.param(DELETED, id="deleted"),
    pytest.param(ALREADY_CLEANED, id="already_cleaned"),
    pytest.param(ABORTED, id="aborted"),
    pytest.param(NOT_FOUND, id="not_found"),
]

WORKTREE_DELETE_PAYLOAD_CASES = [
    pytest.param(
        DELETED,
        {
            "status": "deleted",
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": True,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="deleted",
    ),
    pytest.param(
        ALREADY_CLEANED,
        {
            "status": "already_cleaned",
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": False,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="already_cleaned",
    ),
    pytest.param(
        ABORTED,
        {
            "status": "aborted",
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": False,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="aborted",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "worktree_id": "dovo_missing",
            "worktree": None,
            "deleted": False,
            "warnings": [],
            "errors": ["Worktree 'dovo_missing' not found."],
            "error_code": None,
            "fixes": ["Run `dovo worktree list` to see known worktrees"],
        },
        id="not_found",
    ),
]


class WorktreeDeleteFormatterTests:
    """Tier 2 presentation contract tests for WorktreeDeleteFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_DELETE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeDeleteResult, WorktreeDeleteResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WorktreeDeleteFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_DELETE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeDeleteResult, WorktreeDeleteResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeDeleteFormatter, case.data, case.render_expectations)
