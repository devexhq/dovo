"""Integration tests for SandboxDetector and stale sandbox classification service."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.harness import WorkspaceBuilder
from worktree.common.filesystem import WorkspacePaths
from worktree.common.filesystem.models import RepositoryPaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.db import SandboxesRepository, SandboxStatus
from worktree.core.git.exceptions import GitCommandError
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.sandbox import Sandbox
from worktree.core.sandbox.models import (
    SandboxDetectionStatus,
    StaleSandboxCategory,
)
from worktree.core.sandbox.services.detector import SandboxDetector, detect_stale_sandboxes


@pytest.fixture
def detector_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "detector_ws").with_git().with_database().build()


@pytest.fixture
def detector_workspace_paths(detector_workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for detector_workspace."""
    return resolve_workspace_paths(RepositoryPaths.from_root(detector_workspace), resolve_global_paths(None))


def _repo(paths: WorkspacePaths) -> SandboxesRepository:
    """Build a SandboxesRepository explicitly scoped to paths."""
    return SandboxesRepository(db_path=paths.database_file, project_id=paths.project_id)


class SandboxDetectorBaselineTests:
    """Baseline tests for SandboxDetector on clean workspaces and active sandboxes."""

    def test_detect_clean_workspace_reports_zero_stale(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Clean repository with no sandboxes should return OK with 0 stale items."""
        db = _repo(detector_workspace_paths)
        detector = SandboxDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == SandboxDetectionStatus.OK
        assert len(result.items) == 0
        assert len(result.errors) == 0
        assert result.active_sandbox_count == 0

    def test_active_sandbox_is_excluded_from_all_categories(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Valid active sandboxes must be recorded in active count and excluded from stale items."""
        db = _repo(detector_workspace_paths)
        manager = Sandbox(detector_workspace_paths, db)

        create_result = manager.create(session_id="sbx_active_123")
        assert create_result.ok

        detector = SandboxDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == SandboxDetectionStatus.OK
        assert len(result.items) == 0
        assert len(result.errors) == 0
        assert result.active_sandbox_count == 1


class SandboxDetectorCategoryTests:
    """Category-specific tests for worktree refs, orphaned directories, DB records, and branches."""

    def test_stale_worktree_ref_is_detected(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Stale worktree administrative entries and their unattached branches should be detected."""
        db = _repo(detector_workspace_paths)
        target = detector_workspace / ".worktree" / "sandboxes" / "sbx_wt1"
        GitRunner.worktree_add(
            detector_workspace,
            target_path=target,
            branch="worktree/sandbox-sbx_wt1",
            base_ref="main",
        )
        shutil.rmtree(target)

        detector = SandboxDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == SandboxDetectionStatus.OK
        assert len(result.items) == 2
        assert len(result.errors) == 0

        wt_item = next(i for i in result.items if i.category == StaleSandboxCategory.STALE_WORKTREE_REF)
        assert wt_item.identifier == str(target)
        assert wt_item.path == target
        assert wt_item.branch_name == "worktree/sandbox-sbx_wt1"
        assert "gitdir" in wt_item.reason

        branch_item = next(i for i in result.items if i.category == StaleSandboxCategory.STALE_BRANCH)
        assert branch_item.identifier == "worktree/sandbox-sbx_wt1"
        assert branch_item.branch_name == "worktree/sandbox-sbx_wt1"
        assert (
            branch_item.reason
            == "Sandbox branch 'worktree/sandbox-sbx_wt1' is not attached to any active sandbox or worktree"
        )

    def test_orphaned_directories_classified_by_dirty_state_and_db_status(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Orphaned directories should be classified with their dirty state and DB reconciliation reason."""
        db = _repo(detector_workspace_paths)
        sandboxes_dir = detector_workspace / ".worktree" / "sandboxes"
        sandboxes_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = sandboxes_dir / "sbx_clean"
        clean_dir.mkdir()

        dirty_dir = sandboxes_dir / "sbx_dirty"
        GitRunner.worktree_add(
            detector_workspace,
            target_path=dirty_dir,
            branch="worktree/sandbox-sbx_dirty",
            base_ref="main",
        )
        (dirty_dir / "uncommitted.txt").write_text("wip", encoding="utf-8")

        cleaned_dir = sandboxes_dir / "sbx_cleaned"
        cleaned_dir.mkdir()
        db.create(
            id="sbx_cleaned",
            branch_name="worktree/sandbox-sbx_cleaned",
            base_commit="abc",
            sandbox_path=cleaned_dir,
        )
        db.update_status("sbx_cleaned", SandboxStatus.CLEANED)

        detector = SandboxDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == SandboxDetectionStatus.OK
        assert len(result.items) == 3

        clean_item = next(i for i in result.items if i.identifier == "sbx_clean")
        assert clean_item.category == StaleSandboxCategory.ORPHANED_DIRECTORY
        assert clean_item.path == clean_dir
        assert clean_item.reason == "Sandbox directory 'sbx_clean' is not tracked in the database"

        cleaned_item = next(i for i in result.items if i.identifier == "sbx_cleaned")
        assert cleaned_item.category == StaleSandboxCategory.ORPHANED_DIRECTORY
        assert cleaned_item.path == cleaned_dir
        assert cleaned_item.branch_name == "worktree/sandbox-sbx_cleaned"
        assert cleaned_item.session_id == "sbx_cleaned"
        assert cleaned_item.reason == "Sandbox directory 'sbx_cleaned' has database status 'cleaned'"

        dirty_item = next(i for i in result.items if i.identifier == "sbx_dirty")
        assert dirty_item.category == StaleSandboxCategory.ORPHANED_DIRECTORY
        assert dirty_item.path == dirty_dir
        assert dirty_item.is_dirty is True
        assert dirty_item.dirty_file_count == 1
        assert dirty_item.reason == "Sandbox directory 'sbx_dirty' is not tracked in the database"

    def test_stale_db_record_is_detected(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Active database records with missing sandbox directories should be detected."""
        db = _repo(detector_workspace_paths)
        missing_path = detector_workspace / ".worktree" / "sandboxes" / "sbx_missing"
        db.create(
            id="sbx_missing",
            branch_name="worktree/sandbox-sbx_missing",
            base_commit="abc",
            sandbox_path=missing_path,
        )

        detector = SandboxDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == SandboxDetectionStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleSandboxCategory.STALE_DB_RECORD
        assert item.identifier == "sbx_missing"
        assert item.path == missing_path
        assert item.branch_name == "worktree/sandbox-sbx_missing"
        assert item.session_id == "sbx_missing"
        assert item.reason == "Active database record 'sbx_missing' has missing sandbox path on disk"

    def test_stale_branch_is_detected(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Unattached sandbox branches matching worktree/sandbox-* should be detected."""
        db = _repo(detector_workspace_paths)
        branch_name = "worktree/sandbox-sbx_abandoned"
        GitRunner.run(["branch", branch_name], detector_workspace)

        detector = SandboxDetector(detector_workspace, db)

        result = detector.detect()

        assert result.status == SandboxDetectionStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleSandboxCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.branch_name == branch_name
        assert item.reason == f"Sandbox branch '{branch_name}' is not attached to any active sandbox or worktree"


class SandboxDetectorFacadeTests:
    """Tests verifying helper function and Sandbox facade method agree."""

    def test_detect_stale_sandboxes_helper_and_sandbox_facade_agree(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """Helper and facade methods return equivalent results on clean workspace."""
        db = _repo(detector_workspace_paths)
        manager = Sandbox(detector_workspace_paths, db)

        res_helper = detect_stale_sandboxes(detector_workspace, db)
        res_manager = manager.detect()

        assert res_helper.status == SandboxDetectionStatus.OK
        assert len(res_helper.items) == 0
        assert res_manager.status == SandboxDetectionStatus.OK
        assert len(res_manager.items) == 0


class SandboxDetectorFailureTests:
    """Tests verifying error handling when git or database operations fail."""

    def test_detect_returns_git_failed_on_worktree_list_error(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """When GitRunner.worktree_list fails with GitCommandError, status should be GIT_FAILED."""
        db = _repo(detector_workspace_paths)
        detector = SandboxDetector(detector_workspace, db)

        with patch.object(
            GitRunner,
            "worktree_list",
            side_effect=GitCommandError(["git", "worktree", "list"], 1, "", "fatal error"),
        ):
            result = detector.detect()

        assert result.status == SandboxDetectionStatus.GIT_FAILED
        assert result.errors == [
            "Failed to list git worktrees (GIT_FAILED): Git execution failed ('git git worktree list'): fatal error"
        ]

    def test_detect_returns_error_on_database_failure(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        """When database listing fails, status should be ERROR with description."""
        db = _repo(detector_workspace_paths)
        detector = SandboxDetector(detector_workspace, db)

        with patch.object(db, "list", side_effect=RuntimeError("database locked")):
            result = detector.detect()

        assert result.status == SandboxDetectionStatus.ERROR
        assert result.errors == ["Failed to query sandboxes from database: database locked"]
