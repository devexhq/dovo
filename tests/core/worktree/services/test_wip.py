"""Contract tests for overlaying uncommitted WIP files into a worktree."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.services.wip import apply_wip_to_worktree
from tests.harness import WorkspaceBuilder


class ApplyWipToWorktreeErrorTests:
    @pytest.mark.parametrize(
        ("case", "error_code"),
        [
            pytest.param("missing_path", ErrorCode.WORKTREE_MISSING, id="missing_path"),
            pytest.param("os_error", ErrorCode.WORKTREE_WIP_COPY_FAILED, id="os_error"),
        ],
    )
    def test_apply_wip_raises_worktree_error_with_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, error_code: ErrorCode
    ) -> None:
        """Raise WorktreeError with WORKTREE_MISSING and message "worktree path does not exist: <dest>" for a missing target, and WORKTREE_WIP_COPY_FAILED with message "denied" and a PermissionError __cause__ when shutil.copy2 raises PermissionError("denied") for an untracked file."""
        source_root = WorkspaceBuilder(tmp_path / "source").with_git().build()
        (source_root / "untracked.txt").write_text("wip\n", encoding="utf-8")
        dest = tmp_path / "worktree"
        if case == "os_error":
            dest.mkdir()

            def deny_copy(src: Path, dst: Path) -> None:
                raise PermissionError("denied")

            monkeypatch.setattr("dovo.core.worktree.services.wip.shutil.copy2", deny_copy)

        with pytest.raises(WorktreeError) as excinfo:
            apply_wip_to_worktree(git=GitRunner(), source_root=source_root, worktree_path=dest)

        assert excinfo.value.error_code == error_code
        if case == "missing_path":
            assert excinfo.value.message == f"worktree path does not exist: {dest.resolve()}"
        else:
            assert excinfo.value.message == "denied"
            assert isinstance(excinfo.value.__cause__, PermissionError)
