"""Integration tests for WorktreeDetector and stale worktree classification service."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.models import RepositoryPaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitCommandError
from dovo.core.git.runner import GitRunner
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree import Worktree
from dovo.core.worktree.models import (
    StaleWorktreeCategory,
    WorktreeDetectionStatus,
)
from dovo.core.worktree.services.detector import WorktreeDetector, detect_stale_worktrees
from tests.harness import WorkspaceBuilder


@pytest.fixture
def detector_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "detector_ws").with_git().with_database().build()


@pytest.fixture
def detector_workspace_paths(detector_workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for detector_workspace."""
    return resolve_workspace_paths(RepositoryPaths.from_root(detector_workspace), resolve_global_paths(None))


def _repo(paths: WorkspacePaths) -> WorktreesRepository:
    """Build a WorktreesRepository explicitly scoped to paths."""
    return WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)


class WorktreeDetectorBaselineTests:
    """Baseline tests for WorktreeDetector on clean workspaces and active worktrees."""

    def test_detect_clean_workspace_reports_zero_stale(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Clean repository with no worktrees should return OK with 0 stale items."""
        db = _repo(detector_workspace_paths)
        detector = WorktreeDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 0
        assert len(result.errors) == 0
        assert result.active_worktree_count == 0

    def test_active_worktree_is_excluded_from_all_categories(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Valid active worktrees must be recorded in active count and excluded from stale items."""
        db = _repo(detector_workspace_paths)
        manager = Worktree(detector_workspace_paths, db)

        create_result = manager.create(session_id="dovo_active_123")
        assert create_result.ok

        detector = WorktreeDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 0
        assert len(result.errors) == 0
        assert result.active_worktree_count == 1


class WorktreeDetectorCategoryTests:
    """Category-specific tests for worktree refs, orphaned directories, DB records, and branches."""

    def test_stale_worktree_ref_is_detected(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Stale worktree administrative entries and their unattached branches should be detected."""
        db = _repo(detector_workspace_paths)
        target = detector_workspace / ".dovo" / "worktrees" / "dovo_stale1"
        GitRunner.worktree_add(
            detector_workspace,
            target_path=target,
            branch="dovo/dovo_stale1",
            base_ref="main",
        )
        shutil.rmtree(target)

        detector = WorktreeDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 2
        assert len(result.errors) == 0

        worktree_item = next(i for i in result.items if i.category == StaleWorktreeCategory.STALE_WORKTREE_REF)
        assert worktree_item.identifier == str(target)
        assert worktree_item.path == target
        assert worktree_item.branch_name == "dovo/dovo_stale1"
        assert "gitdir" in worktree_item.reason

        branch_item = next(i for i in result.items if i.category == StaleWorktreeCategory.STALE_BRANCH)
        assert branch_item.identifier == "dovo/dovo_stale1"
        assert branch_item.branch_name == "dovo/dovo_stale1"
        assert branch_item.reason == "Worktree branch 'dovo/dovo_stale1' is not attached to any active worktree"

    def test_orphaned_directories_classified_by_dirty_state_and_db_status(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Orphaned directories should be classified with their dirty state and DB reconciliation reason."""
        db = _repo(detector_workspace_paths)
        worktrees_dir = detector_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = worktrees_dir / "dovo_clean"
        clean_dir.mkdir()

        dirty_dir = worktrees_dir / "dovo_dirty"
        GitRunner.worktree_add(
            detector_workspace,
            target_path=dirty_dir,
            branch="dovo/dovo_dirty",
            base_ref="main",
        )
        (dirty_dir / "uncommitted.txt").write_text("wip", encoding="utf-8")

        cleaned_dir = worktrees_dir / "dovo_cleaned"
        cleaned_dir.mkdir()
        db.create(
            id="dovo_cleaned",
            branch_name="dovo/dovo_cleaned",
            base_commit="abc",
            worktree_path=cleaned_dir,
        )
        db.update_status("dovo_cleaned", WorktreeStatus.CLEANED)

        detector = WorktreeDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 3

        clean_item = next(i for i in result.items if i.identifier == "dovo_clean")
        assert clean_item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert clean_item.path == clean_dir
        assert clean_item.reason == "Worktree directory 'dovo_clean' is not tracked in the database"

        cleaned_item = next(i for i in result.items if i.identifier == "dovo_cleaned")
        assert cleaned_item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert cleaned_item.path == cleaned_dir
        assert cleaned_item.branch_name == "dovo/dovo_cleaned"
        assert cleaned_item.session_id == "dovo_cleaned"
        assert cleaned_item.reason == "Worktree directory 'dovo_cleaned' has database status 'cleaned'"

        dirty_item = next(i for i in result.items if i.identifier == "dovo_dirty")
        assert dirty_item.category == StaleWorktreeCategory.ORPHANED_DIRECTORY
        assert dirty_item.path == dirty_dir
        assert dirty_item.is_dirty is True
        assert dirty_item.dirty_file_count == 1
        assert dirty_item.reason == "Worktree directory 'dovo_dirty' is not tracked in the database"

    def test_stale_db_record_is_detected(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Active database records with missing worktree directories should be detected."""
        db = _repo(detector_workspace_paths)
        missing_path = detector_workspace / ".dovo" / "worktrees" / "dovo_missing"
        db.create(
            id="dovo_missing",
            branch_name="dovo/dovo_missing",
            base_commit="abc",
            worktree_path=missing_path,
        )

        detector = WorktreeDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_DB_RECORD
        assert item.identifier == "dovo_missing"
        assert item.path == missing_path
        assert item.branch_name == "dovo/dovo_missing"
        assert item.session_id == "dovo_missing"
        assert item.reason == "Active database record 'dovo_missing' has missing worktree path on disk"

    def test_stale_branch_is_detected(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Unattached worktree branches matching dovo/dovo_* should be detected."""
        db = _repo(detector_workspace_paths)
        branch_name = "dovo/dovo_abandoned"
        GitRunner.run(["branch", branch_name], detector_workspace)

        detector = WorktreeDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.branch_name == branch_name
        assert item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"


class WorktreeDetectorFacadeTests:
    """Tests verifying helper function and Worktree facade method agree."""

    def test_detect_stale_worktrees_helper_and_worktree_facade_agree(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Helper and facade methods return equivalent results on clean workspace."""
        db = _repo(detector_workspace_paths)
        manager = Worktree(detector_workspace_paths, db)

        res_helper = detect_stale_worktrees(detector_workspace, db)
        res_manager = manager.detect()

        assert res_helper.status == WorktreeDetectionStatus.OK
        assert len(res_helper.items) == 0
        assert res_manager.status == WorktreeDetectionStatus.OK
        assert len(res_manager.items) == 0


class WorktreeDetectorFailureTests:
    """Tests verifying error handling when git or database operations fail."""

    def test_detect_returns_git_failed_on_worktree_list_error(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """When GitRunner.worktree_list fails with GitCommandError, status should be GIT_FAILED."""
        db = _repo(detector_workspace_paths)
        detector = WorktreeDetector(detector_workspace, db)

        with patch.object(
            GitRunner,
            "worktree_list",
            side_effect=GitCommandError(["worktree", "list"], 1, "", "fatal error"),
        ):
            result = detector.detect()

        assert result.status == WorktreeDetectionStatus.GIT_FAILED
        assert result.errors == [
            "Failed to list git worktrees (GIT_FAILED): Git execution failed ('git worktree list'): fatal error"
        ]

    def test_detect_returns_error_on_database_failure(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """When database listing fails, status should be ERROR with description."""
        db = _repo(detector_workspace_paths)
        detector = WorktreeDetector(detector_workspace, db)

        with patch.object(db, "list", side_effect=RuntimeError("database locked")):
            result = detector.detect()

        assert result.status == WorktreeDetectionStatus.ERROR
        assert result.errors == ["Failed to query worktrees from database: database locked"]
