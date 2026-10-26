"""Tier 2 presentation contract tests for WorktreeListFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_list import WorktreeListFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.core.db import WorktreeRecord, WorktreeStatus
from dovo.core.worktree.models import WorktreeListResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = WorktreeRecord(
    id="dovo_list1234",
    project_id="proj-a",
    name="list-worktree",
    branch_name="dovo/list",
    base_commit="abc1234",
    worktree_path="/tmp/dovo_list1234",
    status=WorktreeStatus.ACTIVE,
    created_at="2026-08-31T20:00:00Z",
    updated_at="2026-08-31T20:00:00Z",
)

WITH_WORKTREES = FormatterCase.identity(
    data=WorktreeListResult(worktrees=[_RECORD]),
    render_expectations=[_RECORD.id, "list-worktree", _RECORD.branch_name],
)

EMPTY_WORKTREES = FormatterCase.identity(
    data=WorktreeListResult(worktrees=[]),
    render_expectations=["No worktrees found."],
)

_RECORD_LOG = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)

INTERNAL_ERROR = FormatterCase.identity(
    data=WorktreeListResult(
        error_code="INTERNAL_ERROR",
        errors=["Unexpected error during worktree.list: database is locked"],
        diagnostics=[_RECORD_LOG],
    ),
    render_expectations=[
        "Unexpected error during worktree.list: database is locked",
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)

WORKTREE_LIST_CASES = [
    pytest.param(WITH_WORKTREES, id="with_worktrees"),
    pytest.param(EMPTY_WORKTREES, id="empty_worktrees"),
    pytest.param(INTERNAL_ERROR, id="internal_error"),
]

WORKTREE_LIST_PAYLOAD_CASES = [
    pytest.param(
        WITH_WORKTREES,
        {
            "worktrees": [
                {
                    "id": "dovo_list1234",
                    "project_id": "proj-a",
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
            "remediations": [],
            "diagnostics": [],
        },
        id="with_worktrees",
    ),
    pytest.param(
        EMPTY_WORKTREES,
        {
            "worktrees": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
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
        assert_json_payload_matches_published_shape(WorktreeListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_LIST_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeListResult, WorktreeListResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeListFormatter, case.data, case.render_expectations)
