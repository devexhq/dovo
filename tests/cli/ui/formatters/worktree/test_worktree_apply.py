"""Tier 2 presentation contract tests for WorktreeApplyFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_apply import WorktreeApplyFormatter
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeApplyStatus,
    WorktreeApplyStrategy,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

APPLIED_SQUASH = FormatterCase(
    data=WorktreeApplyResult(
        status=WorktreeApplyStatus.OK,
        worktree_id="dovo_1",
        strategy=WorktreeApplyStrategy.SQUASH,
        commit_sha="abc1234",
        cleaned_up=True,
    ),
    view=WorktreeApplyResult(
        status=WorktreeApplyStatus.OK,
        worktree_id="dovo_1",
        strategy=WorktreeApplyStrategy.SQUASH,
        commit_sha="abc1234",
        cleaned_up=True,
    ),
    render_expectations=["dovo_1", "squash", "abc1234"],
)

APPLIED_PATCH = FormatterCase(
    data=WorktreeApplyResult(
        status=WorktreeApplyStatus.OK,
        worktree_id="dovo_2",
        strategy=WorktreeApplyStrategy.PATCH,
        touched_files=["src/main.py"],
        cleaned_up=False,
    ),
    view=WorktreeApplyResult(
        status=WorktreeApplyStatus.OK,
        worktree_id="dovo_2",
        strategy=WorktreeApplyStrategy.PATCH,
        touched_files=["src/main.py"],
        cleaned_up=False,
    ),
    render_expectations=["dovo_2", "patch"],
)

FAILED_CONFLICT = FormatterCase(
    data=WorktreeApplyResult(
        status=WorktreeApplyStatus.GIT_FAILED,
        worktree_id="dovo_3",
        strategy=WorktreeApplyStrategy.SQUASH,
        conflicting_files=["src/conflict.py"],
        errors=["Merge conflict in src/conflict.py"],
        fixes=["Resolve manually"],
    ),
    view=WorktreeApplyResult(
        status=WorktreeApplyStatus.GIT_FAILED,
        worktree_id="dovo_3",
        strategy=WorktreeApplyStrategy.SQUASH,
        conflicting_files=["src/conflict.py"],
        errors=["Merge conflict in src/conflict.py"],
        fixes=["Resolve manually"],
    ),
    render_expectations=["Merge conflict in src/conflict.py", "Resolve manually"],
)

WORKTREE_APPLY_CASES = [
    pytest.param(APPLIED_SQUASH, id="applied_squash"),
    pytest.param(APPLIED_PATCH, id="applied_patch"),
    pytest.param(FAILED_CONFLICT, id="failed_conflict"),
]

WORKTREE_APPLY_PAYLOAD_CASES = [
    pytest.param(
        APPLIED_SQUASH,
        {
            "status": "ok",
            "worktree_id": "dovo_1",
            "strategy": "squash",
            "touched_files": [],
            "conflicting_files": [],
            "cleaned_up": True,
            "commit_sha": "abc1234",
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="applied_squash",
    ),
    pytest.param(
        APPLIED_PATCH,
        {
            "status": "ok",
            "worktree_id": "dovo_2",
            "strategy": "patch",
            "touched_files": ["src/main.py"],
            "conflicting_files": [],
            "cleaned_up": False,
            "commit_sha": None,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="applied_patch",
    ),
    pytest.param(
        FAILED_CONFLICT,
        {
            "status": "git_failed",
            "worktree_id": "dovo_3",
            "strategy": "squash",
            "touched_files": [],
            "conflicting_files": ["src/conflict.py"],
            "cleaned_up": False,
            "commit_sha": None,
            "warnings": [],
            "errors": ["Merge conflict in src/conflict.py"],
            "error_code": None,
            "fixes": ["Resolve manually"],
        },
        id="failed_conflict",
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
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WorktreeApplyFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WORKTREE_APPLY_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreeApplyResult, WorktreeApplyResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreeApplyFormatter, case.data, case.render_expectations)
