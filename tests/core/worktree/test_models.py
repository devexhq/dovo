"""Contract tests for worktree result models."""

from __future__ import annotations

import pytest

from dovo.core.worktree.models import (
    WorktreeCleanupResult,
    WorktreeCreateResult,
    WorktreeDeleteResult,
    WorktreeListResult,
    WorktreePruneResult,
    WorktreePruneStatus,
    WorktreeShowResult,
)


class WorktreePruneResultOkTests:
    @pytest.mark.parametrize(
        ("status", "error_code", "ok"),
        [
            pytest.param(WorktreePruneStatus.OK, None, True, id="ok"),
            pytest.param(
                WorktreePruneStatus.PARTIAL_FAILURE, "WORKTREE_PRUNE_ITEMS_FAILED", False, id="partial_failure"
            ),
            pytest.param(None, "GIT_FAILED", False, id="stopped"),
        ],
    )
    def test_ok_is_true_exactly_when_error_code_is_none(
        self, status: WorktreePruneStatus | None, error_code: str | None, ok: bool
    ) -> None:
        """Return ok == (error_code is None) for WorktreePruneResult."""
        result = WorktreePruneResult(status=status, error_code=error_code)

        assert result.ok is ok


class WorktreeResultOkTests:
    @pytest.mark.parametrize(
        "model_cls",
        [WorktreeShowResult, WorktreeListResult, WorktreeCreateResult, WorktreeCleanupResult, WorktreeDeleteResult],
    )
    @pytest.mark.parametrize("error_code", [None, "INTERNAL_ERROR"])
    def test_ok_is_true_exactly_when_error_code_is_none(
        self,
        model_cls: type[
            WorktreeShowResult
            | WorktreeListResult
            | WorktreeCreateResult
            | WorktreeCleanupResult
            | WorktreeDeleteResult
        ],
        error_code: str | None,
    ) -> None:
        """Return ok == (error_code is None) for each converted worktree result."""
        result = model_cls(error_code=error_code)

        assert result.ok is (error_code is None)
