"""Integration tests for WorktreeDetector and stale worktree classification service."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitCommandError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree
from dovo.core.worktree.models import (
    StaleWorktreeCategory,
    WorktreeDetectionStatus,
)
from dovo.core.worktree.services.detector import WorktreeDetector, detect_stale_worktrees
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id
from tests.harness.workspace_paths import initialized_workspace_paths


@pytest.fixture
def detector_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "detector_ws").with_git().with_database().build()


@pytest.fixture
def detector_workspace_paths(detector_workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for detector_workspace."""
    return initialized_workspace_paths(detector_workspace)


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
        db = _repo(detector_workspace_paths)
        detector = WorktreeDetector(detector_workspace, db, git=GitRunner())

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
        db = _repo(detector_workspace_paths)
        manager = Worktree(detector_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        create_result = manager.create(session_id="dovo_active_123")
        assert create_result.ok

        detector = WorktreeDetector(detector_workspace, db, git=GitRunner())

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
        db = _repo(detector_workspace_paths)
        target = detector_workspace / ".dovo" / "worktrees" / "dovo_stale1"
        GitRunner().worktree_add(
            detector_workspace,
            target_path=target,
            branch="dovo/dovo_stale1",
            base_ref="main",
        )
        shutil.rmtree(target)

        detector = WorktreeDetector(detector_workspace, db, git=GitRunner())

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
        db = _repo(detector_workspace_paths)
        worktrees_dir = detector_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = worktrees_dir / "dovo_clean"
        clean_dir.mkdir()

        dirty_dir = worktrees_dir / "dovo_dirty"
        GitRunner().worktree_add(
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

        detector = WorktreeDetector(detector_workspace, db, git=GitRunner())

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
        db = _repo(detector_workspace_paths)
        missing_path = detector_workspace / ".dovo" / "worktrees" / "dovo_missing"
        db.create(
            id="dovo_missing",
            branch_name="dovo/dovo_missing",
            base_commit="abc",
            worktree_path=missing_path,
        )

        detector = WorktreeDetector(detector_workspace, db, git=GitRunner())

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
        db = _repo(detector_workspace_paths)
        branch_name = "dovo/dovo_abandoned"
        GitRunner().run(["branch", branch_name], detector_workspace)

        detector = WorktreeDetector(detector_workspace, db, git=GitRunner())

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.branch_name == branch_name
        assert item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"


class WorktreeDetectorEntrypointTests:
    """Tests verifying helper function and Worktree entrypoint method agree."""

    def test_detect_stale_worktrees_helper_and_worktree_entrypoint_agree(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
    ) -> None:
        db = _repo(detector_workspace_paths)
        manager = Worktree(detector_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        res_helper = detect_stale_worktrees(detector_workspace, db, git=GitRunner())
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
        db = _repo(detector_workspace_paths)
        failure = GitCommandError(["worktree", "list"], 1, "", "fatal error")
        detector = WorktreeDetector(detector_workspace, db, git=FakeGitRunner({("worktree", "list"): failure}))

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.GIT_FAILED
        assert result.errors == [
            "Failed to list git worktrees (GIT_FAILED): Git execution failed ('git worktree list'): fatal error"
        ]

    @pytest.mark.parametrize(
        ("target", "prefix"),
        [
            pytest.param("worktree_list", "Unexpected error while listing git worktrees: boom", id="worktree_list"),
            pytest.param("db_list", "Failed to query worktrees from database: boom", id="db_list"),
        ],
    )
    def test_detect_unexpected_failure_returns_error_with_hint(
        self,
        detector_workspace: Path,
        detector_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        target: str,
        prefix: str,
    ) -> None:
        """Return status error with errors == [f"{prefix}\nDetails logged: ... (event exc_3f9a1c07b2de)"] and one error record for worktree.detect."""
        db = _repo(detector_workspace_paths)
        fake = FakeGitRunner()
        detector = WorktreeDetector(detector_workspace, db, git=fake)
        if target == "worktree_list":
            fake.responses[("worktree", "list")] = RuntimeError("boom")
            result = detector.detect()
        else:
            with patch.object(db, "list", side_effect=RuntimeError("boom")):
                result = detector.detect()

        assert result.status == WorktreeDetectionStatus.ERROR
        assert result.errors == [f"{prefix}{diagnostic_hint(frozen_diagnostic_clock)}"]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.detect"
        assert event["context"] == {"path": str(detector.path)}


class WorktreeDetectorDiagnosticLogTests:
    def test_dirty_state_failure_hints_warning(
        self, detector_workspace: Path, detector_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return ok with warnings == ["Unable to inspect git status for orphaned directory 'dovo_orphan': boom\nDetails logged: ..."] when git status --porcelain raises RuntimeError."""
        fake = FakeGitRunner({("status", "--porcelain"): RuntimeError("boom")})
        detector = WorktreeDetector(detector_workspace, _repo(detector_workspace_paths), git=fake)
        orphan_dir = detector.worktree_base_dir / "dovo_orphan"
        orphan_dir.mkdir(parents=True)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert result.warnings == [
            "Unable to inspect git status for orphaned directory 'dovo_orphan': boom"
            f"{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.detect"
        assert event["context"] == {"path": str(orphan_dir)}

    def test_branch_discovery_failure_records_error_and_keeps_result(
        self, detector_workspace: Path, detector_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return ok with no stale_branch items and warnings == [] when git for-each-ref raises RuntimeError, and write one error record for worktree.detect."""
        GitRunner().run(["branch", "dovo/dovo_stale"], path=detector_workspace)
        fake = FakeGitRunner({("for-each-ref",): RuntimeError("boom")})
        detector = WorktreeDetector(detector_workspace, _repo(detector_workspace_paths), git=fake)

        result = detector.detect()

        assert result.status == WorktreeDetectionStatus.OK
        assert [item for item in result.items if item.category == StaleWorktreeCategory.STALE_BRANCH] == []
        assert result.warnings == []
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.detect"
        assert event["context"] == {"path": str(detector.path)}
