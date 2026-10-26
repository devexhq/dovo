"""Contract tests for worktree result models."""

from __future__ import annotations

import pytest

from dovo.core.worktree.models import WorktreePruneResult, WorktreePruneStatus


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
