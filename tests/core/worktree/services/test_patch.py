"""Contract tests for worktree conflict extraction, diff inspection, and patch application."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository
from dovo.core.db.models import WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeApplyStatus, WorktreeApplyStrategy, WorktreeDiffStatus
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.patch import WorktreePatch, extract_conflicts
from tests.harness import WorkspaceBuilder


class ExtractConflictsTests:
    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            pytest.param("error: patch failed: src/main.py:12\n", ["src/main.py"], id="patch_failed"),
            pytest.param("error: src/main.py: patch does not apply\n", ["src/main.py"], id="does_not_apply"),
            pytest.param("cannot apply binary patch to 'assets/logo.png'\n", ["assets/logo.png"], id="binary_patch"),
            pytest.param(
                "error: src/new.py: already exists in working directory\n", ["src/new.py"], id="already_exists"
            ),
            pytest.param("error: src/gone.py: does not exist in index\n", ["src/gone.py"], id="not_in_index"),
            pytest.param("Applying patch\nnothing to see here\n", [], id="no_conflict_markers"),
        ],
    )
    def test_extracts_conflicting_paths_from_known_git_apply_stderr_patterns(
        self, stderr: str, expected: list[str]
    ) -> None:
        """[tier-1/unit] extract_conflicts: recognizes each documented git apply failure line format and extracts its file path."""
        assert extract_conflicts(stderr) == expected

    def test_deduplicates_and_sorts_multiple_conflicting_paths(self) -> None:
        """[tier-1/unit] extract_conflicts: repeated and out-of-order conflict lines collapse into a sorted, deduplicated list."""
        stderr = "error: patch failed: zeta.py\nerror: patch failed: alpha.py\nerror: patch failed: zeta.py\n"
        assert extract_conflicts(stderr) == ["alpha.py", "zeta.py"]


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a worktree database."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


@pytest.fixture
def worktree_workspace_paths(
    worktree_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's worktree workspace."""
    return workspace_paths_factory(worktree_workspace, None)


def _repo(paths: WorkspacePaths) -> WorktreesRepository:
    """Build a WorktreesRepository explicitly scoped to paths."""
    return WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)


def _create_worktree_with_change(paths: WorkspacePaths, worktree_id: str) -> None:
    """Create a real worktree and commit one file change inside it."""
    lifecycle = WorktreeLifecycle(paths, _repo(paths))
    result = lifecycle.create(session_id=worktree_id)
    assert result.session is not None
    worktree_path = result.session.worktree_path
    (worktree_path / "new_file.txt").write_text("worktree change\n", encoding="utf-8")
    GitRunner.run(["add", "-A"], path=worktree_path)
    GitRunner.run(["commit", "-m", "worktree change"], path=worktree_path)


class WorktreePatchDiffTests:
    def test_unknown_worktree_id_returns_not_found(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.diff: no matching DB record -> NOT_FOUND with the worktree id in errors."""
        patch = WorktreePatch(worktree_workspace_paths, _repo(worktree_workspace_paths))

        result = patch.diff("missing_worktree")

        assert result.status == WorktreeDiffStatus.NOT_FOUND
        assert "missing_worktree" in result.errors[0]

    def test_worktree_directory_missing_on_disk_returns_not_found(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.diff: DB record exists but its worktree_path directory was deleted -> NOT_FOUND."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_gone")
        record = db.get("dovo_gone")
        assert record is not None
        import shutil

        shutil.rmtree(record.worktree_path)
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.diff("dovo_gone")

        assert result.status == WorktreeDiffStatus.NOT_FOUND

    def test_worktree_with_no_changes_returns_empty_diff(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.diff: worktree created with no further commits -> EMPTY_DIFF."""
        db = _repo(worktree_workspace_paths)
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)
        create_result = lifecycle.create(session_id="dovo_empty")
        assert create_result.session is not None
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.diff("dovo_empty")

        assert result.status == WorktreeDiffStatus.EMPTY_DIFF

    def test_worktree_with_committed_change_returns_ok_with_touched_files(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.diff: worktree with one committed file change -> OK, files_changed lists it, diff_text is non-empty."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_diffme")
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.diff("dovo_diffme")

        assert result.status == WorktreeDiffStatus.OK
        assert "new_file.txt" in result.files_changed
        assert result.diff_text.strip() != ""


class WorktreePatchApplyTests:
    def test_unknown_worktree_id_returns_not_found(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: no matching DB record -> NOT_FOUND."""
        patch = WorktreePatch(worktree_workspace_paths, _repo(worktree_workspace_paths))

        result = patch.apply("missing_worktree")

        assert result.status == WorktreeApplyStatus.NOT_FOUND

    def test_already_merged_worktree_returns_already_merged_without_reapplying(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: DB record status is MERGED -> ALREADY_MERGED, no git apply attempted."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_merged")
        db.update_status("dovo_merged", WorktreeStatus.MERGED)
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.apply("dovo_merged")

        assert result.status == WorktreeApplyStatus.ALREADY_MERGED

    def test_dirty_main_repo_without_allow_dirty_returns_main_repo_dirty(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: uncommitted changes in the main workspace and allow_dirty=False -> MAIN_REPO_DIRTY, apply is refused."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_dirty_main")
        (worktree_workspace / "uncommitted.txt").write_text("wip", encoding="utf-8")
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.apply("dovo_dirty_main")

        assert result.status == WorktreeApplyStatus.MAIN_REPO_DIRTY

    def test_worktree_with_no_changes_returns_empty_diff(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: worktree has no commits beyond base -> EMPTY_DIFF, nothing applied."""
        db = _repo(worktree_workspace_paths)
        lifecycle = WorktreeLifecycle(worktree_workspace_paths, db)
        create_result = lifecycle.create(session_id="dovo_empty_apply")
        assert create_result.session is not None
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.apply("dovo_empty_apply")

        assert result.status == WorktreeApplyStatus.EMPTY_DIFF

    def test_dry_run_reports_ok_without_modifying_main_workspace(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: dry_run=True -> OK with a dry-run warning, and the target file is not created in the main workspace."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_dry_run")
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.apply("dovo_dry_run", dry_run=True)

        assert result.status == WorktreeApplyStatus.OK
        assert any("dry run" in w.lower() for w in result.warnings)
        assert not (worktree_workspace / "new_file.txt").exists()

    def test_patch_strategy_applies_changes_and_marks_worktree_merged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: PATCH strategy applies the worktree's file change into the main workspace and updates DB status to MERGED."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_apply_patch")
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.apply("dovo_apply_patch", strategy=WorktreeApplyStrategy.PATCH)

        assert result.status == WorktreeApplyStatus.OK
        assert (worktree_workspace / "new_file.txt").read_text(encoding="utf-8") == "worktree change\n"
        record = db.get("dovo_apply_patch")
        assert record is not None
        assert record.status == WorktreeStatus.MERGED

    def test_squash_strategy_creates_single_commit_with_default_message(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/integration] WorktreePatch.apply: SQUASH strategy without an explicit message commits with the default 'dovo: apply changes from worktree <id>' message and returns the new commit sha."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_squash")
        patch = WorktreePatch(worktree_workspace_paths, db)

        result = patch.apply("dovo_squash", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.status == WorktreeApplyStatus.OK
        assert result.commit_sha is not None
        log_message = GitRunner.run(["log", "-1", "--format=%s"], path=worktree_workspace).strip()
        assert log_message == "dovo: apply changes from worktree dovo_squash"
