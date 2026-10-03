"""Tier 2 presentation contract tests for PrunedItemFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.sandbox.pruned_item import PrunedItemFormatter
from dovo.cli.ui.formatters.sandbox.sandbox_views import PrunedItemView
from dovo.core.sandbox.models import (
    PruneAction,
    PrunedItem,
    StaleSandboxCategory,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

DRY_RUN_STALE_BRANCH = FormatterCase(
    data=PrunedItem(
        category=StaleSandboxCategory.STALE_BRANCH,
        identifier="feature/1",
        action=PruneAction.PRUNED,
        reason="Would prune stale branch",
    ),
    view=PrunedItemView(
        category=StaleSandboxCategory.STALE_BRANCH,
        category_label="stale branch",
        identifier="feature/1",
        action=PruneAction.PRUNED,
        is_dry_run=True,
        path=None,
        branch_name=None,
        session_id=None,
        reason="Would prune stale branch",
        error=None,
    ),
    render_expectations=["feature/1", "stale branch"],
)

PRUNED_ORPHANED_DIRECTORY = FormatterCase(
    data=PrunedItem(
        category=StaleSandboxCategory.ORPHANED_DIRECTORY,
        identifier="sbx_12345678",
        action=PruneAction.PRUNED,
        path=Path("/tmp/sbx_12345678"),
        reason="Cleaned up orphaned directory",
    ),
    view=PrunedItemView(
        category=StaleSandboxCategory.ORPHANED_DIRECTORY,
        category_label="orphaned directory",
        identifier="sbx_12345678",
        action=PruneAction.PRUNED,
        is_dry_run=False,
        path=Path("/tmp/sbx_12345678"),
        branch_name=None,
        session_id=None,
        reason="Cleaned up orphaned directory",
        error=None,
    ),
    render_expectations=["sbx_12345678", "orphaned directory"],
)

SKIPPED_DIRTY_DIRECTORY = FormatterCase(
    data=PrunedItem(
        category=StaleSandboxCategory.ORPHANED_DIRECTORY,
        identifier="sbx_dirty",
        action=PruneAction.SKIPPED,
        path=Path("/tmp/sbx_dirty"),
        reason="Contains uncommitted changes",
    ),
    view=PrunedItemView(
        category=StaleSandboxCategory.ORPHANED_DIRECTORY,
        category_label="orphaned directory",
        identifier="sbx_dirty",
        action=PruneAction.SKIPPED,
        is_dry_run=False,
        path=Path("/tmp/sbx_dirty"),
        branch_name=None,
        session_id=None,
        reason="Contains uncommitted changes",
        error=None,
    ),
    render_expectations=["sbx_dirty", "orphaned directory"],
)

FAILED_STALE_WORKTREE_REF = FormatterCase(
    data=PrunedItem(
        category=StaleSandboxCategory.STALE_WORKTREE_REF,
        identifier="/tmp/sbx",
        action=PruneAction.FAILED,
        error="Permission denied",
    ),
    view=PrunedItemView(
        category=StaleSandboxCategory.STALE_WORKTREE_REF,
        category_label="stale worktree ref",
        identifier="/tmp/sbx",
        action=PruneAction.FAILED,
        is_dry_run=False,
        path=None,
        branch_name=None,
        session_id=None,
        reason="",
        error="Permission denied",
    ),
    render_expectations=["/tmp/sbx", "stale worktree ref", "Permission denied"],
)

STALE_DB_RECORD = FormatterCase(
    data=PrunedItem(
        category=StaleSandboxCategory.STALE_DB_RECORD,
        identifier="sbx_rec",
        action=PruneAction.PRUNED,
        session_id="session-123",
        branch_name="feature/test",
        reason="Removed dead record",
    ),
    view=PrunedItemView(
        category=StaleSandboxCategory.STALE_DB_RECORD,
        category_label="stale db record",
        identifier="sbx_rec",
        action=PruneAction.PRUNED,
        is_dry_run=False,
        path=None,
        session_id="session-123",
        branch_name="feature/test",
        reason="Removed dead record",
        error=None,
    ),
    render_expectations=["sbx_rec", "stale db record"],
)

PRUNED_ITEM_CASES = [
    pytest.param(DRY_RUN_STALE_BRANCH, id="dry_run_stale_branch"),
    pytest.param(PRUNED_ORPHANED_DIRECTORY, id="pruned_orphaned_directory"),
    pytest.param(SKIPPED_DIRTY_DIRECTORY, id="skipped_dirty_directory"),
    pytest.param(FAILED_STALE_WORKTREE_REF, id="failed_stale_worktree_ref"),
    pytest.param(STALE_DB_RECORD, id="stale_db_record"),
]

PRUNED_ITEM_PAYLOAD_CASES = [
    pytest.param(
        DRY_RUN_STALE_BRANCH,
        {
            "category": "stale_branch",
            "category_label": "stale branch",
            "identifier": "feature/1",
            "action": "pruned",
            "is_dry_run": True,
            "path": None,
            "branch_name": None,
            "session_id": None,
            "reason": "Would prune stale branch",
            "error": None,
        },
        id="dry_run_stale_branch",
    ),
    pytest.param(
        FAILED_STALE_WORKTREE_REF,
        {
            "category": "stale_worktree_ref",
            "category_label": "stale worktree ref",
            "identifier": "/tmp/sbx",
            "action": "failed",
            "is_dry_run": False,
            "path": None,
            "branch_name": None,
            "session_id": None,
            "reason": "",
            "error": "Permission denied",
        },
        id="failed_stale_worktree_ref",
    ),
]


class PrunedItemFormatterTests:
    """Presentation contract tests for PrunedItemFormatter."""

    @pytest.mark.parametrize("case", PRUNED_ITEM_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[PrunedItem, PrunedItemView]) -> None:
        """Verify transform derives the exact PrunedItemView model representation."""
        assert_transform_derives_expected_view(PrunedItemFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), PRUNED_ITEM_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[PrunedItem, PrunedItemView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(PrunedItemFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", PRUNED_ITEM_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[PrunedItem, PrunedItemView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(PrunedItemFormatter, case.data, case.render_expectations)
