"""Tier 2 presentation contract tests for WorktreeShowFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_show import WorktreeShowFormatter
from dovo.core.db import WorktreeRecord, WorktreeStatus
from dovo.core.worktree.models import (
    WorktreeShowResult,
    WorktreeShowStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = WorktreeRecord(
    id="dovo_test1234",
    name="test-worktree",
    branch_name="dovo/test",
    base_commit="abc1234",
    worktree_path="/tmp/dovo_test1234",
    status=WorktreeStatus.ACTIVE,
    created_at="2026-08-31T20:00:00Z",
    updated_at="2026-08-31T20:00:00Z",
)

FOUND_ACTIVE = FormatterCase(
    data=WorktreeShowResult(
        status=WorktreeShowStatus.OK,
        worktree=_RECORD,
        disk_present=True,
    ),
    view=WorktreeShowResult(
        status=WorktreeShowStatus.OK,
        worktree=_RECORD,
        disk_present=True,
    ),
    render_expectations=[_RECORD.id, "test-worktree", _RECORD.branch_name, _RECORD.base_commit],
)

RECONCILED_MISSING_DISK = FormatterCase(
    data=WorktreeShowResult(
        status=WorktreeShowStatus.OK,
        worktree=_RECORD,
        disk_present=False,
        reconciled=True,
    ),
    view=WorktreeShowResult(
        status=WorktreeShowStatus.OK,
        worktree=_RECORD,
        disk_present=False,
        reconciled=True,
    ),
    render_expectations=[_RECORD.id, "test-worktree", _RECORD.branch_name, _RECORD.base_commit],
)

NOT_FOUND = FormatterCase(
    data=WorktreeShowResult(
        status=WorktreeShowStatus.NOT_FOUND,
        errors=["Worktree 'dovo_missing' not found."],
        fixes=["Run `dovo worktree list` to see known worktrees"],
    ),
    view=WorktreeShowResult(
        status=WorktreeShowStatus.NOT_FOUND,
        errors=["Worktree 'dovo_missing' not found."],
        fixes=["Run `dovo worktree list` to see known worktrees"],
    ),
    render_expectations=["Worktree 'dovo_missing' not found.", "Run `dovo worktree list` to see known worktrees"],
)

WORKTREE_SHOW_CASES = [
    pytest.param(FOUND_ACTIVE, id="found_active"),
    pytest.param(RECONCILED_MISSING_DISK, id="reconciled_missing_disk"),
    pytest.param(NOT_FOUND, id="not_found"),
]

WORKTREE_SHOW_PAYLOAD_CASES = [
    pytest.param(
        FOUND_ACTIVE,
        {
            "status": "ok",
            "worktree": {
                "id": "dovo_test1234",
                "name": "test-worktree",
                "branch_name": "dovo/test",
                "base_commit": "abc1234",
                "worktree_path": "/tmp/dovo_test1234",
                "status": "active",
                "created_at": "2026-08-31T20:00:00Z",
                "updated_at": "2026-08-31T20:00:00Z",
            },
            "disk_present": True,
            "reconciled": False,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="found_active",
    ),
    pytest.param(
        RECONCILED_MISSING_DISK,
        {
            "status": "ok",
            "worktree": {
                "id": "dovo_test1234",
                "name": "test-worktree",
                "branch_name": "dovo/test",
                "base_commit": "abc1234",
                "worktree_path": "/tmp/dovo_test1234",
                "status": "active",
                "created_at": "2026-08-31T20:00:00Z",
                "updated_at": "2026-08-31T20:00:00Z",
            },
            "disk_present": False,
            "reconciled": True,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="reconciled_missing_disk",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "worktree": None,
            "disk_present": False,
            "reconciled": False,
            "warnings": [],
            "errors": ["Worktree 'dovo_missing' not found."],
            "error_code": None,
            "fixes": ["Run `dovo worktree list` to see known worktrees"],
        },
        id="not_found",
    ),
]


class WorktreeShowFormatterTests:
    """Tier 2 presentation contract tests for WorktreeShowFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_SHOW_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeShowResult, WorktreeShowResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(WorktreeShowFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_SHOW_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeShowResult, WorktreeShowResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeShowFormatter, case.data, case.render_expectations)
