from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    WorktreeApplyStatus,
    WorktreeApplyStrategy,
    WorktreeCreateStatus,
)
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.patch import WorktreePatch, extract_conflicts
from tests.harness.builders import WorkspaceBuilder


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


@pytest.fixture
def worktree_workspace_paths(
    worktree_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's worktree workspace."""
    return workspace_paths_factory(worktree_workspace, None)


class WorktreeConflictExtractionTests:
    """Integration tests verifying git apply stderr line parsing."""

    @pytest.mark.parametrize(
        ("stderr_input", "expected_conflicts"),
        [
            pytest.param(
                "error: patch failed: src/main.py:12\nerror: src/main.py: patch does not apply",
                ["src/main.py"],
                id="patch_failed_and_does_not_apply",
            ),
            pytest.param(
                "error: cannot apply binary patch to 'assets/logo.png' without full index line",
                ["assets/logo.png"],
                id="binary_patch_failure",
            ),
            pytest.param(
                "error: new_file.txt: already exists in working directory\nerror: deleted_file.txt: does not exist in index",
                ["deleted_file.txt", "new_file.txt"],
                id="already_exists_and_not_in_index",
            ),
            pytest.param(
                "error: patch failed: a.py:1\nerror: patch failed: b.py:5\nerror: a.py: patch does not apply",
                ["a.py", "b.py"],
                id="multiline_deduplication",
            ),
            pytest.param(
                "",
                [],
                id="empty_stderr",
            ),
        ],
    )
    def test_extract_conflicts_parses_git_apply_stderr_lines(
        self,
        stderr_input: str,
        expected_conflicts: list[str],
    ) -> None:
        """Extract conflicting file paths from varied git apply stderr output lines."""
        assert extract_conflicts(stderr_input) == expected_conflicts


class WorktreeApplyRollbackTests:
    """Integration tests verifying working tree rollback on conflict during patch apply."""

    def test_worktree_patch_apply_rolls_back_partial_changes_on_conflict(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
    ) -> None:
        """Conflict during patch application rolls back partial changes and preserves working tree."""
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)
        patch_service = WorktreePatch(worktree_workspace_paths, db, lifecycle=lifecycle)

        (worktree_workspace / "target.py").write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
        GitRunner.add_all(worktree_workspace)
        GitRunner.commit(worktree_workspace, "Add target.py")
        initial_commit = GitRunner.rev_parse(worktree_workspace, rev="HEAD")

        create_result = lifecycle.create(session_id="dovo_conflict")
        assert create_result.status == WorktreeCreateStatus.OK

        worktree_dir = worktree_workspace / ".dovo" / "worktrees" / "dovo_conflict"
        (worktree_dir / "target.py").write_text("line 1\nworktree edit\nline 3\n", encoding="utf-8")
        GitRunner.add_all(worktree_dir)
        GitRunner.commit(worktree_dir, "Worktree change to target.py")

        (worktree_workspace / "target.py").write_text("line 1\nconflicting main edit\nline 3\n", encoding="utf-8")
        GitRunner.add_all(worktree_workspace)
        GitRunner.commit(worktree_workspace, "Main change causing conflict")

        initial_tree = (worktree_workspace / "target.py").read_text(encoding="utf-8")

        result = patch_service.apply("dovo_conflict")

        assert result.status == WorktreeApplyStatus.CONFLICT
        assert result.worktree_id == "dovo_conflict"
        assert result.strategy == WorktreeApplyStrategy.PATCH
        assert result.touched_files == []
        assert result.conflicting_files == ["target.py"]
        assert result.cleaned_up is False
        assert result.commit_sha is None
        assert result.errors == [
            "Cannot apply worktree dovo_conflict: conflicts detected.\nConflicting files:\n  • target.py"
        ]
        assert result.warnings == []
        assert result.fixes == [
            "Inspect worktree differences with `dovo worktree diff dovo_conflict`",
            "Resolve conflicts in the main workspace or worktree",
        ]

        assert (worktree_workspace / "target.py").read_text(encoding="utf-8") == initial_tree

        record = db.get("dovo_conflict")
        assert record is not None
        assert record.id == "dovo_conflict"
        assert record.name is None
        assert record.branch_name == "dovo/dovo_conflict"
        assert record.base_commit == initial_commit
        assert record.worktree_path == (worktree_workspace / ".dovo" / "worktrees" / "dovo_conflict").resolve()
        assert record.status == WorktreeStatus.CONFLICT
