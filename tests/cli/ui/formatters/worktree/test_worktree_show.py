"""Tier 2 presentation contract tests for WorktreeShowFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_show import WorktreeShowFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.db import WorktreeRecord, WorktreeStatus
from dovo.core.worktree.models import WorktreeShowResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = WorktreeRecord(
    id="dovo_test1234",
    project_id="proj-a",
    name="test-worktree",
    branch_name="dovo/test",
    base_commit="abc1234",
    worktree_path="/tmp/dovo_test1234",
    status=WorktreeStatus.ACTIVE,
    created_at="2026-08-31T20:00:00Z",
    updated_at="2026-08-31T20:00:00Z",
)

FOUND_ACTIVE = FormatterCase.identity(
    data=WorktreeShowResult(
        worktree=_RECORD,
        disk_present=True,
    ),
    render_expectations=[_RECORD.id, "test-worktree", _RECORD.branch_name, _RECORD.base_commit],
)

RECONCILED_MISSING_DISK = FormatterCase.identity(
    data=WorktreeShowResult(
        worktree=_RECORD,
        disk_present=False,
        reconciled=True,
    ),
    render_expectations=[_RECORD.id, "test-worktree", _RECORD.branch_name, _RECORD.base_commit],
)

NOT_FOUND = FormatterCase.identity(
    data=WorktreeShowResult(
        error_code="WORKTREE_NOT_FOUND",
        errors=["Worktree 'dovo_missing' not found."],
        remediations=[
            Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")
        ],
    ),
    render_expectations=[
        "Worktree 'dovo_missing' not found.",
        "Fix:",
        "- Run 'dovo worktree list' to see known worktrees",
    ],
)

_RECORD_LOG = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)

INTERNAL_ERROR = FormatterCase.identity(
    data=WorktreeShowResult(
        error_code="INTERNAL_ERROR",
        errors=["Unexpected error during worktree.show: database is locked"],
        diagnostics=[_RECORD_LOG],
    ),
    render_expectations=[
        "Unexpected error during worktree.show: database is locked",
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)

WORKTREE_SHOW_CASES = [
    pytest.param(FOUND_ACTIVE, id="found_active"),
    pytest.param(RECONCILED_MISSING_DISK, id="reconciled_missing_disk"),
    pytest.param(NOT_FOUND, id="not_found"),
    pytest.param(INTERNAL_ERROR, id="internal_error"),
]

WORKTREE_SHOW_PAYLOAD_CASES = [
    pytest.param(
        FOUND_ACTIVE,
        {
            "worktree": {
                "id": "dovo_test1234",
                "project_id": "proj-a",
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
            "remediations": [],
            "diagnostics": [],
        },
        id="found_active",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "worktree": None,
            "disk_present": False,
            "reconciled": False,
            "warnings": [],
            "errors": ["Worktree 'dovo_missing' not found."],
            "error_code": "WORKTREE_NOT_FOUND",
            "remediations": [
                {"message": "Run 'dovo worktree list' to see known worktrees", "command": "dovo worktree list"}
            ],
            "diagnostics": [],
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
        assert_json_payload_matches_published_shape(WorktreeShowFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_SHOW_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeShowResult, WorktreeShowResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeShowFormatter, case.data, case.render_expectations)
