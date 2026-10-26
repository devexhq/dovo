"""Presentation contract tests for WorktreePruneFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest

from dovo.cli.ui.formatters.worktree.worktree_prune import WorktreePruneFormatter
from dovo.cli.ui.formatters.worktree.worktree_views import (
    PrunedItemView,
    WorktreePruneView,
)
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.worktree.models import (
    PruneAction,
    PrunedItem,
    StaleWorktreeCategory,
    WorktreePruneResult,
    WorktreePruneStatus,
)
from tests.harness.diagnostic_log import details_line
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _make_pruned_item_view(**overrides: object) -> PrunedItemView:
    """Construct a PrunedItemView with all fields explicitly specified."""
    defaults: dict[str, object] = {
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
    return PrunedItemView.model_validate({**defaults, **overrides})


def _make_prune_view(**overrides: object) -> WorktreePruneView:
    """Construct a WorktreePruneView with all fields explicitly specified."""
    defaults: dict[str, object] = {
        "status": WorktreePruneStatus.OK,
        "dry_run": False,
        "force": False,
        "items": [],
        "pruned_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "error_code": None,
        "errors": [],
        "warnings": [],
        "remediations": [],
        "diagnostics": [],
    }
    return WorktreePruneView.model_validate({**defaults, **overrides})


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
        status=WorktreePruneStatus.PARTIAL_FAILURE,
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
        error_code="WORKTREE_PRUNE_ITEMS_FAILED",
        errors=["Ref lock failure"],
    ),
    view=_make_prune_view(
        status=WorktreePruneStatus.PARTIAL_FAILURE,
        force=True,
        items=[
            _make_pruned_item_view(
                category=StaleWorktreeCategory.STALE_BRANCH,
                category_label="stale branch",
                identifier="feature/stale",
                action=PruneAction.PRUNED,
                reason="Cleaned up branch",
            ),
            _make_pruned_item_view(
                category=StaleWorktreeCategory.ORPHANED_DIRECTORY,
                category_label="orphaned directory",
                identifier="dovo_dirty",
                action=PruneAction.SKIPPED,
                reason="Contains uncommitted changes",
            ),
            _make_pruned_item_view(
                category=StaleWorktreeCategory.STALE_WORKTREE_REF,
                category_label="stale worktree ref",
                identifier="/tmp/dovo_ref",
                action=PruneAction.FAILED,
                error="Ref locked",
            ),
        ],
        pruned_count=1,
        skipped_count=1,
        failed_count=1,
        error_code="WORKTREE_PRUNE_ITEMS_FAILED",
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
        dry_run=True,
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
    ),
    render_expectations=["feature/dry"],
)

WITH_WARNINGS_AND_REMEDIATIONS = FormatterCase(
    data=WorktreePruneResult(
        status=WorktreePruneStatus.OK,
        dry_run=False,
        force=False,
        warnings=["Warning message"],
        remediations=[Remediation(message="Fix suggestion")],
    ),
    view=_make_prune_view(
        warnings=["Warning message"],
        remediations=[Remediation(message="Fix suggestion")],
    ),
    render_expectations=["No stale worktrees found."],
)

DIAGNOSTIC_RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)

WITH_DIAGNOSTICS = FormatterCase(
    data=WorktreePruneResult(
        status=WorktreePruneStatus.OK,
        dry_run=False,
        force=False,
        diagnostics=[DIAGNOSTIC_RECORD],
    ),
    view=_make_prune_view(diagnostics=[DIAGNOSTIC_RECORD]),
    render_expectations=[details_line(DIAGNOSTIC_RECORD.log_path)],
)

STOPPED = FormatterCase(
    data=WorktreePruneResult(
        status=None,
        dry_run=False,
        force=False,
        error_code="GIT_FAILED",
        errors=["Git execution failed ('git worktree list'): fatal error"],
    ),
    view=_make_prune_view(
        status=None,
        error_code="GIT_FAILED",
        errors=["Git execution failed ('git worktree list'): fatal error"],
    ),
    render_expectations=["Error: Git execution failed ('git worktree list'): fatal error"],
)

PRUNE_CASES = [
    pytest.param(EMPTY_PRUNE, id="empty_prune"),
    pytest.param(PRUNED_MULTIPLE_ITEMS, id="pruned_multiple_items"),
    pytest.param(DRY_RUN_PRUNE, id="dry_run_prune"),
    pytest.param(WITH_WARNINGS_AND_REMEDIATIONS, id="with_warnings_and_remediations"),
    pytest.param(WITH_DIAGNOSTICS, id="with_diagnostics"),
    pytest.param(STOPPED, id="stopped"),
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
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="empty_prune",
    ),
    pytest.param(
        PRUNED_MULTIPLE_ITEMS,
        {
            "status": "partial_failure",
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
            "error_code": "WORKTREE_PRUNE_ITEMS_FAILED",
            "errors": ["Ref lock failure"],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="pruned_multiple_items",
    ),
    pytest.param(
        WITH_DIAGNOSTICS,
        {
            "status": "ok",
            "dry_run": False,
            "force": False,
            "items": [],
            "pruned_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [
                {
                    "event_id": "exc_3f9a1c07b2de",
                    "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json",
                }
            ],
        },
        id="with_diagnostics",
    ),
    pytest.param(
        STOPPED,
        {
            "status": None,
            "dry_run": False,
            "force": False,
            "items": [],
            "pruned_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "error_code": "GIT_FAILED",
            "errors": ["Git execution failed ('git worktree list'): fatal error"],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="stopped",
    ),
]


class WorktreePruneFormatterTests:
    """Presentation contract tests for WorktreePruneFormatter."""

    @pytest.mark.parametrize("case", PRUNE_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[WorktreePruneResult, WorktreePruneView]) -> None:
        assert_transform_derives_expected_view(WorktreePruneFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), PRUNE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WorktreePruneResult, WorktreePruneView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(WorktreePruneFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", PRUNE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WorktreePruneResult, WorktreePruneView]
    ) -> None:
        assert_rich_render_shows_every_view_value(WorktreePruneFormatter, case.data, case.render_expectations)
