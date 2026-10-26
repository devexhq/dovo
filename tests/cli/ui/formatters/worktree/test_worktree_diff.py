"""Tier 2 presentation contract tests for WorktreeDiffFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_diff import WorktreeDiffFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.worktree.models import WorktreeDiffResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

OK_WITH_DIFF = FormatterCase.identity(
    data=WorktreeDiffResult(
        worktree_id="dovo_diff",
        diff_text="+new_line",
        files_changed=["new_file.txt"],
    ),
    render_expectations=["+new_line"],
)

EMPTY_SUCCESS = FormatterCase.identity(
    data=WorktreeDiffResult(worktree_id="dovo_empty"),
    render_expectations=["dovo_empty", "has no changes compared to base commit."],
)

NOT_FOUND = FormatterCase.identity(
    data=WorktreeDiffResult(
        worktree_id="dovo_missing",
        error_code="WORKTREE_NOT_FOUND",
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

RECORD = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)

FAILED_WITH_DIAGNOSTICS = FormatterCase.identity(
    data=WorktreeDiffResult(
        worktree_id="dovo_boom",
        error_code="INTERNAL_ERROR",
        errors=["Unexpected error during worktree.diff: boom"],
        diagnostics=[RECORD],
    ),
    render_expectations=[
        "Unexpected error during worktree.diff: boom",
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)

WORKTREE_DIFF_CASES = [
    pytest.param(OK_WITH_DIFF, id="ok_with_diff"),
    pytest.param(EMPTY_SUCCESS, id="empty_success"),
    pytest.param(NOT_FOUND, id="not_found"),
    pytest.param(FAILED_WITH_DIAGNOSTICS, id="failed_with_diagnostics"),
]

WORKTREE_DIFF_PAYLOAD_CASES = [
    pytest.param(
        OK_WITH_DIFF,
        {
            "worktree_id": "dovo_diff",
            "diff_text": "+new_line",
            "stat_text": "",
            "files_changed": ["new_file.txt"],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="ok_with_diff",
    ),
    pytest.param(
        EMPTY_SUCCESS,
        {
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
        id="empty_success",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "worktree_id": "dovo_missing",
            "diff_text": "",
            "stat_text": "",
            "files_changed": [],
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
        FAILED_WITH_DIAGNOSTICS,
        {
            "worktree_id": "dovo_boom",
            "diff_text": "",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": ["Unexpected error during worktree.diff: boom"],
            "error_code": "INTERNAL_ERROR",
            "remediations": [],
            "diagnostics": [
                {"event_id": "exc_3f9a1c07b2de", "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"}
            ],
        },
        id="failed_with_diagnostics",
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
