"""Integration tests for WorktreePruner and safe prune execution service."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import LockTimeoutError
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree
from dovo.core.worktree.models import (
    PruneAction,
    StaleWorktreeCategory,
    WorktreeDetectionResult,
    WorktreeDetectionStatus,
    WorktreePruneStatus,
)
from dovo.core.worktree.services.pruner import WorktreePruner, prune_stale_worktrees
from tests.harness import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths


@pytest.fixture
def pruner_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "pruner_ws").with_git().with_database().build()


@pytest.fixture
def pruner_workspace_paths(pruner_workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for pruner_workspace."""
    return initialized_workspace_paths(pruner_workspace)


def _repo(paths: WorkspacePaths) -> WorktreesRepository:
    """Build a WorktreesRepository explicitly scoped to paths."""
    return WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)


class WorktreePrunerBaselineTests:
    """Baseline tests for WorktreePruner on clean or simulated workspaces."""

    def test_prune_clean_workspace_is_a_noop(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        """Pruning a clean workspace should return OK with 0 items."""
        db = _repo(pruner_workspace_paths)
        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.OK
        assert result.dry_run is False
        assert result.force is False
        assert len(result.items) == 0
        assert len(result.errors) == 0

    def test_prune_dry_run_reports_all_categories_without_mutation(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
    ) -> None:
        """Dry-run reports planned actions across categories without mutating disk, DB, or git."""
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = worktrees_dir / "dovo_clean"
        clean_dir.mkdir()

        dirty_dir = worktrees_dir / "dovo_dirty"
        GitRunner.worktree_add(
            pruner_workspace,
            target_path=dirty_dir,
            branch="dovo/dovo_dirty",
            base_ref="main",
        )
        (dirty_dir / "dirty.txt").write_text("wip", encoding="utf-8")

        missing_path = worktrees_dir / "dovo_missing"
        db.create(
            id="dovo_missing",
            branch_name="dovo/dovo_missing",
            base_commit="abc",
            worktree_path=missing_path,
        )

        subprocess.run(
            ["git", "branch", "dovo/dovo_dead"],
            cwd=pruner_workspace,
            check=True,
            capture_output=True,
        )

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune(dry_run=True, force=False)

        assert result.status == WorktreePruneStatus.OK
        assert result.dry_run is True
        assert result.force is False
        assert len(result.items) == 4

        clean_item = next(i for i in result.items if i.identifier == "dovo_clean")
        assert clean_item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert clean_item.action == PruneAction.PRUNED
        assert clean_item.path == clean_dir
        assert clean_item.reason == "Would prune: Worktree directory 'dovo_clean' is not tracked in the database"

        dirty_item = next(i for i in result.items if i.identifier == "dovo_dirty")
        assert dirty_item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert dirty_item.action == PruneAction.SKIPPED
        assert dirty_item.path == dirty_dir
        assert (
            dirty_item.reason == "Orphaned directory 'dovo_dirty' contains uncommitted changes; use --force to delete"
        )

        missing_item = next(i for i in result.items if i.identifier == "dovo_missing")
        assert missing_item.category == StaleWorktreeCategory.STALE_DB_RECORD
        assert missing_item.action == PruneAction.PRUNED
        assert missing_item.path == missing_path
        assert missing_item.branch_name == "dovo/dovo_missing"
        assert missing_item.session_id == "dovo_missing"
        assert (
            missing_item.reason
            == "Would prune: Active database record 'dovo_missing' has missing worktree path on disk"
        )

        branch_item = next(i for i in result.items if i.identifier == "dovo/dovo_dead")
        assert branch_item.category == StaleWorktreeCategory.STALE_BRANCH
        assert branch_item.action == PruneAction.PRUNED
        assert branch_item.branch_name == "dovo/dovo_dead"
        assert (
            branch_item.reason == "Would prune: Worktree branch 'dovo/dovo_dead' is not attached to any active worktree"
        )
        assert clean_dir.exists()
        assert dirty_dir.exists()
        record = db.get("dovo_missing")
        assert record is not None and record.status == WorktreeStatus.ACTIVE
        branches = GitRunner.list_branches(pruner_workspace, pattern="dovo/dovo_*")
        assert "dovo/dovo_dead" in branches


class WorktreePrunerSafetyTests:
    """Safety tests verifying preservation of dirty orphans without force."""

    def test_dirty_orphan_skipped_without_force(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        """Dirty orphan directory must be preserved with SKIPPED status when force=False."""
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        dirty_dir = worktrees_dir / "dovo_dirty_orphan"
        GitRunner.worktree_add(
            pruner_workspace,
            target_path=dirty_dir,
            branch="dovo/dovo_dirty_orphan",
            base_ref="main",
        )
        (dirty_dir / "wip.txt").write_text("changes", encoding="utf-8")

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune(force=False)

        assert result.status == WorktreePruneStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert item.identifier == "dovo_dirty_orphan"
        assert item.action == PruneAction.SKIPPED
        assert item.path == dirty_dir
        assert (
            item.reason == "Orphaned directory 'dovo_dirty_orphan' contains uncommitted changes; use --force to delete"
        )
        assert dirty_dir.exists()

    def test_dirty_orphan_deleted_with_force(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        """Dirty orphan directory must be removed when force=True."""
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        dirty_dir = worktrees_dir / "dovo_dirty_forced"
        GitRunner.worktree_add(
            pruner_workspace,
            target_path=dirty_dir,
            branch="dovo/dovo_dirty_forced",
            base_ref="main",
        )
        (dirty_dir / "wip.txt").write_text("changes", encoding="utf-8")

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune(force=True)

        assert result.status == WorktreePruneStatus.OK
        assert result.force is True
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert item.identifier == "dovo_dirty_forced"
        assert item.action == PruneAction.PRUNED
        assert item.path == dirty_dir
        assert item.reason == "Worktree directory 'dovo_dirty_forced' is not tracked in the database"
        assert not dirty_dir.exists()

    def test_clean_orphan_deleted_without_force(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        """Clean orphan directory must be deleted even with force=False."""
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = worktrees_dir / "dovo_clean_orphan"
        clean_dir.mkdir()

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune(force=False)

        assert result.status == WorktreePruneStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert item.identifier == "dovo_clean_orphan"
        assert item.action == PruneAction.PRUNED
        assert item.path == clean_dir
        assert item.reason == "Worktree directory 'dovo_clean_orphan' is not tracked in the database"
        assert not clean_dir.exists()


class WorktreePrunerCategoryTests:
    """Category-specific tests for worktree refs, DB records, and branches."""

    def test_stale_worktree_ref_is_pruned(self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths) -> None:
        """Stale worktree administrative entries should be pruned."""
        db = _repo(pruner_workspace_paths)
        target = pruner_workspace / ".dovo" / "worktrees" / "dovo_stale"
        GitRunner.worktree_add(
            pruner_workspace,
            target_path=target,
            branch="dovo/dovo_stale",
            base_ref="main",
        )
        shutil.rmtree(target)

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.OK
        assert len(result.items) == 2

        worktree_item = next(i for i in result.items if i.category == StaleWorktreeCategory.STALE_WORKTREE_REF)
        assert worktree_item.identifier == str(target)
        assert worktree_item.action == PruneAction.PRUNED
        assert worktree_item.path == target
        assert worktree_item.branch_name == "dovo/dovo_stale"
        assert "gitdir" in worktree_item.reason

        branch_item = next(i for i in result.items if i.category == StaleWorktreeCategory.STALE_BRANCH)
        assert branch_item.identifier == "dovo/dovo_stale"
        assert branch_item.action == PruneAction.PRUNED
        assert branch_item.branch_name == "dovo/dovo_stale"
        assert branch_item.reason == "Worktree branch 'dovo/dovo_stale' is not attached to any active worktree"

    def test_stale_db_record_is_reconciled_to_cleaned(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        """Active DB records with missing paths must be updated to CLEANED."""
        db = _repo(pruner_workspace_paths)
        missing_path = pruner_workspace / ".dovo" / "worktrees" / "dovo_db_stale"
        db.create(
            id="dovo_db_stale",
            branch_name="dovo/dovo_db_stale",
            base_commit="abc",
            worktree_path=missing_path,
        )

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_DB_RECORD
        assert item.identifier == "dovo_db_stale"
        assert item.action == PruneAction.PRUNED
        assert item.path == missing_path
        assert item.branch_name == "dovo/dovo_db_stale"
        assert item.session_id == "dovo_db_stale"
        assert item.reason == "Active database record 'dovo_db_stale' has missing worktree path on disk"

        record = db.get("dovo_db_stale")
        assert record is not None
        assert record.id == "dovo_db_stale"
        assert record.name is None
        assert record.branch_name == "dovo/dovo_db_stale"
        assert record.base_commit == "abc"
        assert record.worktree_path == missing_path
        assert record.status == WorktreeStatus.CLEANED

    def test_stale_branch_is_deleted(self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths) -> None:
        """Stale worktree temporary branches must be deleted."""
        db = _repo(pruner_workspace_paths)
        branch_name = "dovo/dovo_stale_branch"
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=pruner_workspace,
            check=True,
            capture_output=True,
        )

        pruner = WorktreePruner(pruner_workspace, db)

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.action == PruneAction.PRUNED
        assert item.branch_name == branch_name
        assert item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"
        branches = GitRunner.list_branches(pruner_workspace, pattern="dovo/dovo_*")
        assert branch_name not in branches


class WorktreePrunerIdempotencyTests:
    """Tests verifying multi-resource pruning followed by an idempotent re-run."""

    def test_combined_prune_then_idempotent_rerun(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        """Pruning multiple categories followed by a second run must be clean and idempotent."""
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        dir1 = worktrees_dir / "dovo_c1"
        dir1.mkdir()
        db.create(
            id="dovo_c1",
            branch_name="dovo/dovo_c1",
            base_commit="abc",
            worktree_path=dir1,
        )
        db.update_status("dovo_c1", WorktreeStatus.CLEANED)

        branch_name = "dovo/dovo_c2"
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=pruner_workspace,
            check=True,
            capture_output=True,
        )

        manager = Worktree(pruner_workspace_paths, db)
        create_res = manager.create(session_id="dovo_protected")
        assert create_res.ok

        result1 = manager.prune()

        assert result1.status == WorktreePruneStatus.OK
        assert len(result1.items) == 2

        dir_item = next(i for i in result1.items if i.identifier == "dovo_c1")
        assert dir_item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert dir_item.action == PruneAction.PRUNED
        assert dir_item.path == dir1
        assert dir_item.branch_name == "dovo/dovo_c1"
        assert dir_item.session_id == "dovo_c1"
        assert dir_item.reason == "Worktree directory 'dovo_c1' has database status 'cleaned'"

        branch_item = next(i for i in result1.items if i.identifier == branch_name)
        assert branch_item.category == StaleWorktreeCategory.STALE_BRANCH
        assert branch_item.action == PruneAction.PRUNED
        assert branch_item.branch_name == branch_name
        assert branch_item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"

        assert not dir1.exists()
        assert (worktrees_dir / "dovo_protected").exists()

        result2 = manager.prune()

        assert result2.status == WorktreePruneStatus.OK
        assert len(result2.items) == 0
        assert len(result2.errors) == 0


class WorktreePrunerFacadeTests:
    """Tests verifying helper function and Worktree facade method agree."""

    def test_prune_stale_worktrees_helper_and_worktree_facade_agree(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
    ) -> None:
        """Helper and facade methods return equivalent results on clean workspace."""
        db = _repo(pruner_workspace_paths)
        manager = Worktree(pruner_workspace_paths, db)

        res_helper = prune_stale_worktrees(pruner_workspace, db, dry_run=True)
        res_manager = manager.prune(dry_run=True)

        assert res_helper.status == WorktreePruneStatus.OK
        assert res_helper.dry_run is True
        assert len(res_helper.items) == 0

        assert res_manager.status == WorktreePruneStatus.OK
        assert res_manager.dry_run is True
        assert len(res_manager.items) == 0


class WorktreePrunerFailureTests:
    """Tests verifying error handling, detection failures, and lock timeouts."""

    def test_prune_aborts_with_git_failed_when_detection_fails(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
    ) -> None:
        """When detector returns GIT_FAILED, prune should abort with GIT_FAILED status."""
        db = _repo(pruner_workspace_paths)
        pruner = WorktreePruner(pruner_workspace, db)

        with patch.object(
            pruner.detector,
            "detect",
            return_value=WorktreeDetectionResult(
                status=WorktreeDetectionStatus.GIT_FAILED,
                errors=["Git command failed"],
                items=[],
                active_worktree_count=0,
                warnings=[],
                fixes=[],
            ),
        ):
            result = pruner.prune()

        assert result.status == WorktreePruneStatus.GIT_FAILED
        assert result.errors == ["Git command failed"]

    def test_prune_returns_locked_on_workspace_lock_timeout(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
    ) -> None:
        """Workspace lock timeouts should return LOCKED status without crashing."""
        db = _repo(pruner_workspace_paths)
        pruner = WorktreePruner(pruner_workspace, db)

        with patch(
            "dovo.core.worktree.services.pruner.WorkspaceLock.__enter__",
            side_effect=LockTimeoutError("Locked"),
        ):
            result = pruner.prune()

        assert result.status == WorktreePruneStatus.LOCKED
        assert result.errors == ["Failed to acquire workspace lock: Locked"]

    def test_prune_returns_partial_success_on_item_failure(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
    ) -> None:
        """Errors during individual item pruning should produce PARTIAL_SUCCESS."""
        db = _repo(pruner_workspace_paths)
        branch_name = "dovo/dovo_fail_branch"
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=pruner_workspace,
            check=True,
            capture_output=True,
        )

        pruner = WorktreePruner(pruner_workspace, db)

        with patch.object(
            GitRunner,
            "branch_delete",
            side_effect=RuntimeError("Permission denied"),
        ):
            result = pruner.prune()

        assert result.status == WorktreePruneStatus.PARTIAL_SUCCESS
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.action == PruneAction.FAILED
        assert item.branch_name == branch_name
        assert item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"
        assert item.error == f"Failed to delete branch '{branch_name}': Permission denied"

        assert result.errors == [f"Failed to delete branch '{branch_name}': Permission denied"]
