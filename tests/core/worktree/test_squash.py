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
from dovo.core.worktree.services.patch import WorktreePatch
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


class WorktreeSquashApplyTests:
    """Integration tests verifying squash apply strategy and branch cleanup."""

    def test_squash_apply_collapses_multiple_commits_into_single_commit(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
    ) -> None:
        """Squash strategy collapses multiple intermediate worktree commits into single commit with message."""
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)
        patch_service = WorktreePatch(worktree_workspace_paths, db, lifecycle=lifecycle)

        head_before = GitRunner.rev_parse(worktree_workspace, rev="HEAD")

        create_result = lifecycle.create(session_id="dovo_squash")
        assert create_result.status == WorktreeCreateStatus.OK

        worktree_dir = worktree_workspace / ".dovo" / "worktrees" / "dovo_squash"

        (worktree_dir / "feature1.py").write_text("feature 1\n", encoding="utf-8")
        GitRunner.add_all(worktree_dir)
        GitRunner.commit(worktree_dir, "Commit 1 in worktree")

        (worktree_dir / "feature2.py").write_text("feature 2\n", encoding="utf-8")
        GitRunner.add_all(worktree_dir)
        GitRunner.commit(worktree_dir, "Commit 2 in worktree")

        result = patch_service.apply(
            "dovo_squash",
            strategy=WorktreeApplyStrategy.SQUASH,
            message="Squash worktree feature",
        )

        assert result.status == WorktreeApplyStatus.OK
        assert result.worktree_id == "dovo_squash"
        assert result.strategy == WorktreeApplyStrategy.SQUASH
        assert result.touched_files == ["feature1.py", "feature2.py"]
        assert result.conflicting_files == []
        assert result.cleaned_up is False
        assert result.commit_sha is not None
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert GitRunner.rev_parse(worktree_workspace, rev="HEAD") == result.commit_sha
        assert GitRunner.run(["log", "-1", "--format=%B"], worktree_workspace).strip() == "Squash worktree feature"
        assert GitRunner.rev_parse(worktree_workspace, rev="HEAD~1") == head_before
        assert (worktree_workspace / "feature1.py").read_text(encoding="utf-8") == "feature 1\n"
        assert (worktree_workspace / "feature2.py").read_text(encoding="utf-8") == "feature 2\n"

    def test_squash_apply_deletes_worktree_branch_and_marks_applied(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
    ) -> None:
        """Squash apply with delete=True deletes worktree branch, worktree directory, and marks worktree merged."""
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)
        patch_service = WorktreePatch(worktree_workspace_paths, db, lifecycle=lifecycle)

        initial_commit = GitRunner.rev_parse(worktree_workspace, rev="HEAD")

        create_result = lifecycle.create(session_id="dovo_squash_del")
        assert create_result.status == WorktreeCreateStatus.OK

        worktree_dir = worktree_workspace / ".dovo" / "worktrees" / "dovo_squash_del"

        (worktree_dir / "feature.py").write_text("squash feature\n", encoding="utf-8")
        GitRunner.add_all(worktree_dir)
        GitRunner.commit(worktree_dir, "Add feature in worktree")

        result = patch_service.apply(
            "dovo_squash_del",
            strategy=WorktreeApplyStrategy.SQUASH,
            delete=True,
            message="Squash and cleanup",
        )

        assert result.status == WorktreeApplyStatus.OK
        assert result.worktree_id == "dovo_squash_del"
        assert result.strategy == WorktreeApplyStrategy.SQUASH
        assert result.touched_files == ["feature.py"]
        assert result.conflicting_files == []
        assert result.cleaned_up is True
        assert result.commit_sha is not None
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

        assert "dovo/dovo_squash_del" not in GitRunner.list_branches(worktree_workspace)
        assert not (worktree_workspace / ".dovo" / "worktrees" / "dovo_squash_del").exists()

        record = db.get("dovo_squash_del")
        assert record is not None
        assert record.id == "dovo_squash_del"
        assert record.name is None
        assert record.branch_name == "dovo/dovo_squash_del"
        assert record.base_commit == initial_commit
        assert record.worktree_path == (worktree_workspace / ".dovo" / "worktrees" / "dovo_squash_del").resolve()
        assert record.status == WorktreeStatus.MERGED
        assert record.created_at is not None
        assert record.updated_at is not None
