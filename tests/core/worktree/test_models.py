"""Contract tests for worktree result models."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.core.worktree.models import (
    WorktreeApplyResult,
    WorktreeCleanupResult,
    WorktreeCreateResult,
    WorktreeDeleteResult,
    WorktreeDiffResult,
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
                WorktreePruneStatus.PARTIAL_FAILURE, ErrorCode.WORKTREE_PRUNE_FAILED, False, id="partial_failure"
            ),
            pytest.param(None, ErrorCode.GIT_FAILED, False, id="stopped"),
        ],
    )
    def test_ok_is_true_exactly_when_error_code_is_none(
        self, status: WorktreePruneStatus | None, error_code: ErrorCode | None, ok: bool
    ) -> None:
        """Return ok == (error_code is None) for WorktreePruneResult."""
        result = WorktreePruneResult(
            status=status, error_code=error_code, errors=[] if error_code is None else ["boom"]
        )

        assert result.ok is ok


_OkModel = (
    WorktreeShowResult
    | WorktreeListResult
    | WorktreeCreateResult
    | WorktreeCleanupResult
    | WorktreeDeleteResult
    | WorktreeDiffResult
    | WorktreeApplyResult
)


class WorktreeResultOkTests:
    @pytest.mark.parametrize(
        "model_factory",
        [
            pytest.param(WorktreeShowResult, id="show"),
            pytest.param(WorktreeListResult, id="list"),
            pytest.param(WorktreeCreateResult, id="create"),
            pytest.param(WorktreeCleanupResult, id="cleanup"),
            pytest.param(WorktreeDeleteResult, id="delete"),
            pytest.param(partial(WorktreeDiffResult, worktree_id="wt"), id="diff"),
            pytest.param(partial(WorktreeApplyResult, worktree_id="wt"), id="apply"),
        ],
    )
    @pytest.mark.parametrize("error_code", [None, ErrorCode.INTERNAL_ERROR])
    def test_ok_is_true_exactly_when_error_code_is_none(
        self, model_factory: Callable[..., _OkModel], error_code: ErrorCode | None
    ) -> None:
        """Return ok == (error_code is None) for each converted worktree result."""
        result = model_factory(error_code=error_code, errors=[] if error_code is None else ["boom"])

        assert result.ok is (error_code is None)
