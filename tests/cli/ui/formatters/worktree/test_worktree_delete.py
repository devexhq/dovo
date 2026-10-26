"""Tier 2 presentation contract tests for WorktreeDeleteFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_delete import WorktreeDeleteFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.worktree.models import WorktreeDeleteResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

DELETED = FormatterCase.identity(
    data=WorktreeDeleteResult(
        worktree_id="dovo_del",
        deleted=True,
    ),
    render_expectations=["dovo_del"],
)

DELETED_WITH_WARNINGS = FormatterCase.identity(
    data=WorktreeDeleteResult(
        worktree_id="dovo_del",
        deleted=True,
        warnings=["Failed to delete branch 'dovo/dovo_del': boom"],
    ),
    render_expectations=["dovo_del", "Failed to delete branch 'dovo/dovo_del': boom"],
)

ALREADY_CLEANED = FormatterCase.identity(
    data=WorktreeDeleteResult(
        worktree_id="dovo_del",
        already_cleaned=True,
    ),
    render_expectations=["dovo_del", "already cleaned"],
)

CANCELLED = FormatterCase.identity(
    data=WorktreeDeleteResult(
        worktree_id="dovo_del",
        cancelled=True,
    ),
    render_expectations=["Deletion cancelled."],
)

DELETED_WITH_DIAGNOSTICS = FormatterCase.identity(
    data=WorktreeDeleteResult(
        worktree_id="dovo_del",
        deleted=True,
        warnings=["Failed to delete branch 'dovo/dovo_del': boom"],
        diagnostics=[
            DiagnosticRecord(
                event_id="exc_3f9a1c07b2de",
                log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
                level=DiagnosticLevel.ERROR,
            )
        ],
    ),
    render_expectations=[
        "dovo_del",
        "Failed to delete branch 'dovo/dovo_del': boom",
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)

NOT_FOUND = FormatterCase.identity(
    data=WorktreeDeleteResult(
        error_code="WORKTREE_NOT_FOUND",
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

WORKTREE_DELETE_CASES = [
    pytest.param(DELETED, id="deleted"),
    pytest.param(DELETED_WITH_WARNINGS, id="deleted_with_warnings"),
    pytest.param(ALREADY_CLEANED, id="already_cleaned"),
    pytest.param(CANCELLED, id="cancelled"),
    pytest.param(NOT_FOUND, id="not_found"),
    pytest.param(DELETED_WITH_DIAGNOSTICS, id="deleted_with_diagnostics"),
]

WORKTREE_DELETE_PAYLOAD_CASES = [
    pytest.param(
        DELETED,
        {
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": True,
            "already_cleaned": False,
            "cancelled": False,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="deleted",
    ),
    pytest.param(
        DELETED_WITH_WARNINGS,
        {
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": True,
            "already_cleaned": False,
            "cancelled": False,
            "warnings": ["Failed to delete branch 'dovo/dovo_del': boom"],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="deleted_with_warnings",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "worktree_id": "dovo_missing",
            "worktree": None,
            "deleted": False,
            "already_cleaned": False,
            "cancelled": False,
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
    pytest.param(
        ALREADY_CLEANED,
        {
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": False,
            "already_cleaned": True,
            "cancelled": False,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="already_cleaned",
    ),
    pytest.param(
        CANCELLED,
        {
            "worktree_id": "dovo_del",
            "worktree": None,
            "deleted": False,
            "already_cleaned": False,
            "cancelled": True,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="cancelled",
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
        assert_json_payload_matches_published_shape(WorktreeDeleteFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_DELETE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeDeleteResult, WorktreeDeleteResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeDeleteFormatter, case.data, case.render_expectations)
