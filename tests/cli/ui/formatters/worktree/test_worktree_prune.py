"""Presentation contract tests for WorktreePruneFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.worktree.worktree_prune import WorktreePruneFormatter
from dovo.cli.ui.formatters.worktree.worktree_views import (
    PrunedItemView,
    WorktreePruneView,
)
from dovo.core.worktree.models import (
    PruneAction,
    PrunedItem,
    StaleWorktreeCategory,
    WorktreePruneResult,
    WorktreePruneStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _make_pruned_item_view(**overrides: Any) -> PrunedItemView:
    """Helper to construct a PrunedItemView with all fields explicitly specified."""
    defaults: dict[str, Any] = {
        "category": StaleWorktreeCategory.STALE_BRANCH,
        "category_label": "stale branch",
        "identifier": "feature/stale",
        "action": PruneAction.PRUNED,
        "is_dry_run": False,
        "path": None,
        "branch_name": None,
        "session_id": None,
        "reason": "",
        "error": None,
    }
    defaults.update(overrides)
    return PrunedItemView(**defaults)


def _make_prune_view(**overrides: Any) -> WorktreePruneView:
    """Helper to construct a WorktreePruneView with all fields explicitly specified."""
    defaults: dict[str, Any] = {
        "status": WorktreePruneStatus.OK,
        "dry_run": False,
        "force": False,
        "items": [],
        "pruned_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "errors": [],
        "warnings": [],
        "fixes": [],
    }
    defaults.update(overrides)
    return WorktreePruneView(**defaults)


EMPTY_PRUNE = FormatterCase(
    data=WorktreePruneResult(
        status=WorktreePruneStatus.OK,
        dry_run=False,
        force=False,
        items=[],
    ),
    view=_make_prune_view(),
    render_expectations=["No stale worktrees found."],
)

PRUNED_MULTIPLE_ITEMS = FormatterCase(
    data=WorktreePruneResult(
        status=WorktreePruneStatus.PARTIAL_SUCCESS,
        dry_run=False,
        force=True,
        items=[
            PrunedItem(
                category=StaleWorktreeCategory.STALE_BRANCH,
                identifier="feature/stale",
                action=PruneAction.PRUNED,
                reason="Cleaned up branch",
            ),
            PrunedItem(
                category=StaleWorktreeCategory.ORPHANED_DIRECTORY,
                identifier="dovo_dirty",
                action=PruneAction.SKIPPED,
                reason="Contains uncommitted changes",
            ),
            PrunedItem(
                category=StaleWorktreeCategory.STALE_WORKTREE_REF,
                identifier="/tmp/dovo_ref",
                action=PruneAction.FAILED,
                error="Ref locked",
            ),
        ],
        errors=["Ref lock failure"],
    ),
    view=_make_prune_view(
        status=WorktreePruneStatus.PARTIAL_SUCCESS,
        dry_run=False,
        force=True,
        items=[
            _make_pruned_item_view(
                category=StaleWorktreeCategory.STALE_BRANCH,
                category_label="stale branch",
                identifier="feature/stale",
                action=PruneAction.PRUNED,
                is_dry_run=False,
                reason="Cleaned up branch",
            ),
            _make_pruned_item_view(
                category=StaleWorktreeCategory.ORPHANED_DIRECTORY,
                category_label="orphaned directory",
                identifier="dovo_dirty",
                action=PruneAction.SKIPPED,
                is_dry_run=False,
                reason="Contains uncommitted changes",
            ),
            _make_pruned_item_view(
                category=StaleWorktreeCategory.STALE_WORKTREE_REF,
                category_label="stale worktree ref",
                identifier="/tmp/dovo_ref",
                action=PruneAction.FAILED,
                is_dry_run=False,
                reason="",
                error="Ref locked",
            ),
        ],
        pruned_count=1,
        skipped_count=1,
        failed_count=1,
        errors=["Ref lock failure"],
    ),
    render_expectations=["feature/stale", "dovo_dirty", "/tmp/dovo_ref", "Ref lock failure"],
)

DRY_RUN_PRUNE = FormatterCase(
    data=WorktreePruneResult(
        status=WorktreePruneStatus.OK,
        dry_run=True,
        force=False,
        items=[
            PrunedItem(
                category=StaleWorktreeCategory.STALE_BRANCH,
                identifier="feature/dry",
                action=PruneAction.PRUNED,
                reason="Stale branch",
            ),
        ],
    ),
    view=_make_prune_view(
        status=WorktreePruneStatus.OK,
        dry_run=True,
        force=False,
        items=[
            _make_pruned_item_view(
                category=StaleWorktreeCategory.STALE_BRANCH,
                category_label="stale branch",
                identifier="feature/dry",
                action=PruneAction.PRUNED,
                is_dry_run=True,
                reason="Stale branch",
            ),
        ],
        pruned_count=1,
        skipped_count=0,
        failed_count=0,
    ),
    render_expectations=["feature/dry"],
)

WITH_WARNINGS_AND_FIXES = FormatterCase(
    data=WorktreePruneResult(
        status=WorktreePruneStatus.OK,
        dry_run=False,
        force=False,
        warnings=["Warning message"],
        fixes=["Fix suggestion"],
    ),
    view=_make_prune_view(
        warnings=["Warning message"],
        fixes=["Fix suggestion"],
    ),
    render_expectations=["No stale worktrees found."],
)

PRUNE_CASES = [
    pytest.param(EMPTY_PRUNE, id="empty_prune"),
    pytest.param(PRUNED_MULTIPLE_ITEMS, id="pruned_multiple_items"),
    pytest.param(DRY_RUN_PRUNE, id="dry_run_prune"),
    pytest.param(WITH_WARNINGS_AND_FIXES, id="with_warnings_and_fixes"),
]

PRUNE_PAYLOAD_CASES = [
    pytest.param(
        EMPTY_PRUNE,
        {
            "status": "ok",
            "dry_run": False,
            "force": False,
            "items": [],
            "pruned_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="empty_prune",
    ),
    pytest.param(
        PRUNED_MULTIPLE_ITEMS,
        {
            "status": "partial_success",
            "dry_run": False,
            "force": True,
            "items": [
                {
                    "category": "stale_branch",
                    "category_label": "stale branch",
                    "identifier": "feature/stale",
                    "action": "pruned",
                    "is_dry_run": False,
                    "path": None,
                    "branch_name": None,
                    "session_id": None,
                    "reason": "Cleaned up branch",
                    "error": None,
                },
                {
                    "category": "orphaned_directory",
                    "category_label": "orphaned directory",
                    "identifier": "dovo_dirty",
                    "action": "skipped",
                    "is_dry_run": False,
                    "path": None,
                    "branch_name": None,
                    "session_id": None,
                    "reason": "Contains uncommitted changes",
                    "error": None,
                },
                {
                    "category": "stale_worktree_ref",
                    "category_label": "stale worktree ref",
                    "identifier": "/tmp/dovo_ref",
                    "action": "failed",
                    "is_dry_run": False,
                    "path": None,
                    "branch_name": None,
                    "session_id": None,
                    "reason": "",
                    "error": "Ref locked",
                },
            ],
            "pruned_count": 1,
            "skipped_count": 1,
            "failed_count": 1,
            "errors": ["Ref lock failure"],
            "warnings": [],
            "fixes": [],
        },
        id="pruned_multiple_items",
    ),
]


class WorktreePruneFormatterTests:
    """Presentation contract tests for WorktreePruneFormatter."""

    @pytest.mark.parametrize("case", PRUNE_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[WorktreePruneResult, WorktreePruneView]) -> None:
        """Verify transform derives the exact WorktreePruneView model representation."""
        assert_transform_derives_expected_view(WorktreePruneFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), PRUNE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreePruneResult, WorktreePruneView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(WorktreePruneFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", PRUNE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreePruneResult, WorktreePruneView]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WorktreePruneFormatter, case.data, case.render_expectations)
