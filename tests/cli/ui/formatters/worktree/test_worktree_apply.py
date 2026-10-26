"""Tier 2 presentation contract tests for WorktreeApplyFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_apply import WorktreeApplyFormatter
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStrategy,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

APPLIED_SQUASH = FormatterCase.identity(
    data=WorktreeApplyResult(
        worktree_id="dovo_1",
        strategy=WorktreeApplyStrategy.SQUASH,
        commit_sha="abc1234",
        cleaned_up=True,
    ),
    render_expectations=["dovo_1", "squash", "abc1234"],
)

APPLIED_PATCH = FormatterCase.identity(
    data=WorktreeApplyResult(
        worktree_id="dovo_2",
        strategy=WorktreeApplyStrategy.PATCH,
        touched_files=["src/main.py"],
        cleaned_up=False,
    ),
    render_expectations=["dovo_2", "patch"],
)

FAILED_CONFLICT = FormatterCase.identity(
    data=WorktreeApplyResult(
        worktree_id="dovo_3",
        error_code="GIT_FAILED",
        strategy=WorktreeApplyStrategy.SQUASH,
        conflicting_files=["src/conflict.py"],
        errors=["Merge conflict in src/conflict.py"],
        remediations=[Remediation(message="Resolve manually")],
    ),
    render_expectations=["Merge conflict in src/conflict.py", "Fix:", "- Resolve manually"],
)

APPLIED_DELETE_WITH_DIAGNOSTICS = FormatterCase.identity(
    data=WorktreeApplyResult(
        worktree_id="dovo_4",
        strategy=WorktreeApplyStrategy.SQUASH,
        commit_sha="def5678",
        cleaned_up=True,
        warnings=["Failed to delete branch 'dovo/dovo_4': boom"],
        diagnostics=[
            DiagnosticRecord(
                event_id="exc_3f9a1c07b2de",
                log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
                level=DiagnosticLevel.ERROR,
            )
        ],
    ),
    render_expectations=[
        "dovo_4",
        "Failed to delete branch 'dovo/dovo_4': boom",
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
)

ALREADY_MERGED = FormatterCase.identity(
    data=WorktreeApplyResult(
        worktree_id="dovo_5",
        error_code="WORKTREE_ALREADY_MERGED",
        errors=["Worktree 'dovo_5' is already merged."],
    ),
    render_expectations=["Worktree 'dovo_5' is already merged."],
)

WORKTREE_APPLY_CASES = [
    pytest.param(APPLIED_SQUASH, id="applied_squash"),
    pytest.param(APPLIED_PATCH, id="applied_patch"),
    pytest.param(FAILED_CONFLICT, id="failed_conflict"),
    pytest.param(APPLIED_DELETE_WITH_DIAGNOSTICS, id="applied_delete_with_diagnostics"),
    pytest.param(ALREADY_MERGED, id="already_merged"),
]

WORKTREE_APPLY_PAYLOAD_CASES = [
    pytest.param(
        APPLIED_SQUASH,
        {
            "worktree_id": "dovo_1",
            "strategy": "squash",
            "touched_files": [],
            "conflicting_files": [],
            "cleaned_up": True,
            "commit_sha": "abc1234",
            "warnings": [],
            "errors": [],
            "error_code": None,
            "remediations": [],
            "diagnostics": [],
        },
        id="applied_squash",
    ),
    pytest.param(
        FAILED_CONFLICT,
        {
            "worktree_id": "dovo_3",
            "strategy": "squash",
            "touched_files": [],
            "conflicting_files": ["src/conflict.py"],
            "cleaned_up": False,
            "commit_sha": None,
            "warnings": [],
            "errors": ["Merge conflict in src/conflict.py"],
            "error_code": "GIT_FAILED",
            "remediations": [{"message": "Resolve manually", "command": None}],
            "diagnostics": [],
        },
        id="failed_conflict",
    ),
    pytest.param(
        ALREADY_MERGED,
        {
            "worktree_id": "dovo_5",
            "strategy": "patch",
            "touched_files": [],
            "conflicting_files": [],
            "cleaned_up": False,
            "commit_sha": None,
            "warnings": [],
            "errors": ["Worktree 'dovo_5' is already merged."],
            "error_code": "WORKTREE_ALREADY_MERGED",
            "remediations": [],
            "diagnostics": [],
        },
        id="already_merged",
    ),
]


class WorktreeApplyFormatterTests:
    """Tier 2 presentation contract tests for WorktreeApplyFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WORKTREE_APPLY_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreeApplyResult, WorktreeApplyResult],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(WorktreeApplyFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_APPLY_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeApplyResult, WorktreeApplyResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreeApplyFormatter, case.data, case.render_expectations)
