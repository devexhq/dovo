"""Integration tests for WorktreePruner and safe prune execution service."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError, git_command_error
from dovo.core.git.models import GitProcessOutput
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree
from dovo.core.worktree.models import (
    PruneAction,
    StaleWorktreeCategory,
    WorktreePruneStatus,
)
from dovo.core.worktree.services.pruner import WorktreePruner
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id
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
        db = _repo(pruner_workspace_paths)
        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.OK
        assert result.dry_run is False
        assert result.force is False
        assert len(result.items) == 0
        assert len(result.errors) == 0
        assert result.error_code is None

    def test_prune_dry_run_reports_all_categories_without_mutation(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
    ) -> None:
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = worktrees_dir / "dovo_clean"
        clean_dir.mkdir()

        dirty_dir = worktrees_dir / "dovo_dirty"
        GitRunner().worktree_add(
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

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

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
        branches = GitRunner().list_branches(pruner_workspace, pattern="dovo/dovo_*")
        assert "dovo/dovo_dead" in branches


class WorktreePrunerSafetyTests:
    """Safety tests verifying preservation of dirty orphans without force."""

    def test_dirty_orphan_skipped_without_force(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        dirty_dir = worktrees_dir / "dovo_dirty_orphan"
        GitRunner().worktree_add(
            pruner_workspace,
            target_path=dirty_dir,
            branch="dovo/dovo_dirty_orphan",
            base_ref="main",
        )
        (dirty_dir / "wip.txt").write_text("changes", encoding="utf-8")

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

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
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        dirty_dir = worktrees_dir / "dovo_dirty_forced"
        GitRunner().worktree_add(
            pruner_workspace,
            target_path=dirty_dir,
            branch="dovo/dovo_dirty_forced",
            base_ref="main",
        )
        (dirty_dir / "wip.txt").write_text("changes", encoding="utf-8")

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

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
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        clean_dir = worktrees_dir / "dovo_clean_orphan"
        clean_dir.mkdir()

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

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
        db = _repo(pruner_workspace_paths)
        target = pruner_workspace / ".dovo" / "worktrees" / "dovo_stale"
        GitRunner().worktree_add(
            pruner_workspace,
            target_path=target,
            branch="dovo/dovo_stale",
            base_ref="main",
        )
        shutil.rmtree(target)

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

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
        db = _repo(pruner_workspace_paths)
        missing_path = pruner_workspace / ".dovo" / "worktrees" / "dovo_db_stale"
        db.create(
            id="dovo_db_stale",
            branch_name="dovo/dovo_db_stale",
            base_commit="abc",
            worktree_path=missing_path,
        )

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

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
        db = _repo(pruner_workspace_paths)
        branch_name = "dovo/dovo_stale_branch"
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=pruner_workspace,
            check=True,
            capture_output=True,
        )

        pruner = WorktreePruner(pruner_workspace_paths, db, git=GitRunner())

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.OK
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.action == PruneAction.PRUNED
        assert item.branch_name == branch_name
        assert item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"
        branches = GitRunner().list_branches(pruner_workspace, pattern="dovo/dovo_*")
        assert branch_name not in branches


class WorktreePrunerIdempotencyTests:
    """Tests verifying multi-resource pruning followed by an idempotent re-run."""

    def test_combined_prune_then_idempotent_rerun(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths
    ) -> None:
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

        manager = Worktree(pruner_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
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


class WorktreePrunerFailureTests:
    """Tests verifying item failures leave a partial_failure result."""

    def test_prune_returns_partial_failure_on_item_failure(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """Return partial_failure with item.error and result.errors == [f"Failed to delete branch '{branch_name}': Permission denied"], diagnostics naming its record, and one error record for worktree.prune with context {worktree_id: None, branch, path: None}."""
        db = _repo(pruner_workspace_paths)
        branch_name = "dovo/dovo_fail_branch"
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=pruner_workspace,
            check=True,
            capture_output=True,
        )

        pruner = WorktreePruner(
            pruner_workspace_paths, db, git=FakeGitRunner({("branch", "-D"): RuntimeError("Permission denied")})
        )

        result = pruner.prune()

        assert result.status == WorktreePruneStatus.PARTIAL_FAILURE
        assert result.error_code == "WORKTREE_PRUNE_ITEMS_FAILED"
        assert len(result.items) == 1

        item = result.items[0]
        assert item.category == StaleWorktreeCategory.STALE_BRANCH
        assert item.identifier == branch_name
        assert item.action == PruneAction.FAILED
        assert item.branch_name == branch_name
        assert item.reason == f"Worktree branch '{branch_name}' is not attached to any active worktree"
        expected_error = f"Failed to delete branch '{branch_name}': Permission denied"
        assert item.error == expected_error

        assert result.errors == [expected_error]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.prune"
        assert event["context"] == {"worktree_id": None, "branch": branch_name, "path": None}


def _raise_boom(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a repository call that fails with an unexpected RuntimeError("boom")."""
    raise RuntimeError("boom")


class _RemovesDirectory(FakeGitRunner):
    """FakeGitRunner that stands in for a successful git worktree remove by deleting its target directory."""

    def exec(
        self,
        args: Sequence[str],
        path: Path,
        *,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> GitProcessOutput:
        if tuple(args[:2]) == ("worktree", "remove"):
            self.calls.append(tuple(args))
            shutil.rmtree(args[2])
            return GitProcessOutput(0, "", "")
        return super().exec(args, path, input_text=input_text, env=env)


def _make_untracked_orphan(workspace: Path, worktree_id: str) -> Path:
    """Create an orphaned directory under .dovo/worktrees that git does not track."""
    orphan_dir = workspace / ".dovo" / "worktrees" / worktree_id
    orphan_dir.mkdir(parents=True)
    return orphan_dir


class WorktreePrunerDiagnosticLogTests:
    """Tests verifying prune item failures are recorded into diagnostics and recovered fallbacks leave debug records."""

    @pytest.mark.parametrize(
        ("category", "message"),
        [
            pytest.param("stale_worktree_ref", "Failed to prune git worktree ref: boom", id="stale_worktree_ref"),
            pytest.param(
                "stale_db_record", "Failed to update database status for 'dovo_gone': boom", id="stale_db_record"
            ),
            pytest.param(
                "orphan_status", "Failed to update database status for 'dovo_orphan': boom", id="orphan_status"
            ),
            pytest.param(
                "stale_branch_git",
                "Failed to delete branch 'dovo/dovo_x': Git execution failed ('git branch -D dovo/dovo_x'): boom",
                id="stale_branch_git",
            ),
        ],
    )
    def test_prune_item_failure_records_error_into_diagnostics(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        category: str,
        message: str,
    ) -> None:
        """Return partial_failure with the item failed, item.error == message, errors == [message], diagnostics == [diagnostic_record(frozen_diagnostic_clock)], and one error event with operation worktree.prune."""
        db = _repo(pruner_workspace_paths)
        worktrees_dir = pruner_workspace / ".dovo" / "worktrees"
        fake = _RemovesDirectory()
        if category == "stale_worktree_ref":
            target = worktrees_dir / "dovo_gone"
            GitRunner().run(["worktree", "add", "--detach", str(target), "main"], path=pruner_workspace)
            shutil.rmtree(target)
            expected_category = StaleWorktreeCategory.STALE_WORKTREE_REF
            fake.responses[("worktree", "prune")] = RuntimeError("boom")
        elif category == "stale_db_record":
            db.create(
                id="dovo_gone",
                branch_name="dovo/dovo_gone",
                base_commit="abc",
                worktree_path=worktrees_dir / "dovo_gone",
            )
            expected_category = StaleWorktreeCategory.STALE_DB_RECORD
            monkeypatch.setattr(db, "update_status", _raise_boom)
        elif category == "orphan_status":
            orphan_dir = _make_untracked_orphan(pruner_workspace, "dovo_orphan")
            db.create(id="dovo_orphan", branch_name="dovo/dovo_orphan", base_commit="abc", worktree_path=orphan_dir)
            db.update_status("dovo_orphan", WorktreeStatus.CLEANED)
            expected_category = StaleWorktreeCategory.ORPHANED_DIRECTORY
            monkeypatch.setattr(db, "update_status", _raise_boom)
        else:
            GitRunner().run(["branch", "dovo/dovo_x"], path=pruner_workspace)
            expected_category = StaleWorktreeCategory.STALE_BRANCH
            fake.responses[("branch", "-D")] = git_command_error(["branch", "-D", "dovo/dovo_x"], 1, "", "boom")

        result = WorktreePruner(pruner_workspace_paths, db, git=fake).prune()

        assert result.status == WorktreePruneStatus.PARTIAL_FAILURE
        item = next(i for i in result.items if i.category == expected_category)
        assert item.action == PruneAction.FAILED
        assert item.error == message
        assert result.errors == [message]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.prune"

    def test_fallback_git_not_found_writes_no_record(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return ok with the untracked orphan pruned by rmtree and leave <DOVO_HOME>/logs absent when git worktree remove raises a GIT_BINARY_MISSING GitError."""
        orphan_dir = _make_untracked_orphan(pruner_workspace, "dovo_orphan")
        fake = FakeGitRunner({("worktree", "remove"): GitError(ErrorCode.GIT_BINARY_MISSING, "git not found")})

        result = WorktreePruner(pruner_workspace_paths, _repo(pruner_workspace_paths), git=fake).prune()

        assert result.status == WorktreePruneStatus.OK
        assert [(item.identifier, item.action) for item in result.items] == [("dovo_orphan", PruneAction.PRUNED)]
        assert result.diagnostics == []
        assert not orphan_dir.exists()
        assert not resolve_global_paths().logs_dir.exists()

    def test_untracked_orphan_falls_back_to_rmtree_with_debug_record(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return ok with the orphan pruned, its directory gone, no hint in any entry, and one debug record for worktree.prune with exception_type GitError."""
        orphan_dir = _make_untracked_orphan(pruner_workspace, "dovo_orphan")

        result = WorktreePruner(pruner_workspace_paths, _repo(pruner_workspace_paths), git=GitRunner()).prune()

        assert result.status == WorktreePruneStatus.OK
        assert [(item.identifier, item.action, item.error) for item in result.items] == [
            ("dovo_orphan", PruneAction.PRUNED, None)
        ]
        assert result.errors == []
        assert result.diagnostics == []
        assert not orphan_dir.exists()
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.prune"
        assert event["context"] == {"worktree_id": None, "branch": None, "path": str(orphan_dir)}
        assert event["exception_type"] == "GitError"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_orphan_rmtree_failure_records_error_into_diagnostics(
        self, pruner_workspace: Path, pruner_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return partial_failure with item.error starting "Failed to remove directory '<path>': " and carrying no hint when worktrees_dir is read-only, diagnostics naming its record, and one error record for worktree.prune."""
        orphan_dir = _make_untracked_orphan(pruner_workspace, "dovo_orphan")
        orphan_dir.parent.chmod(0o555)
        try:
            result = WorktreePruner(pruner_workspace_paths, _repo(pruner_workspace_paths), git=GitRunner()).prune()
        finally:
            orphan_dir.parent.chmod(0o755)

        assert result.status == WorktreePruneStatus.PARTIAL_FAILURE
        item = result.items[0]
        assert item.action == PruneAction.FAILED
        assert item.error is not None
        assert item.error.startswith(f"Failed to remove directory '{orphan_dir}': ")
        assert "Details logged:" not in item.error
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.prune"
        assert event["exception_type"] == "PermissionError"

    def test_orphan_final_prune_failure_records_debug(
        self,
        pruner_workspace: Path,
        pruner_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """Return ok with the orphan pruned when git worktree remove succeeds and git worktree prune raises RuntimeError, and write one debug record for worktree.prune."""
        orphan_dir = _make_untracked_orphan(pruner_workspace, "dovo_orphan")
        fake = _RemovesDirectory({("worktree", "prune"): RuntimeError("boom")})

        result = WorktreePruner(pruner_workspace_paths, _repo(pruner_workspace_paths), git=fake).prune()

        assert result.status == WorktreePruneStatus.OK
        assert [(item.identifier, item.action) for item in result.items] == [("dovo_orphan", PruneAction.PRUNED)]
        assert result.diagnostics == []
        assert not orphan_dir.exists()
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.prune"
        assert event["exception_type"] == "RuntimeError"


class WorktreePrunerWorkspacePathsTests:
    """Tests verifying prune removes orphans under the worktrees directory named by WorkspacePaths."""

    def test_prune_removes_orphan_under_relocated_worktrees_dir(
        self, pruner_workspace_paths: WorkspacePaths, tmp_path: Path
    ) -> None:
        """Return ok with one PRUNED orphaned_directory item whose path is paths.worktrees_dir / "orphan_1", and remove that directory from disk."""
        paths = pruner_workspace_paths.model_copy(update={"worktrees_dir": tmp_path / "elsewhere"})
        orphan_dir = paths.worktrees_dir / "orphan_1"
        orphan_dir.mkdir(parents=True)
        GitRunner().run(["init"], orphan_dir)

        result = WorktreePruner(paths, _repo(paths), git=GitRunner()).prune()

        assert result.status == WorktreePruneStatus.OK
        assert [(item.category, item.action, item.path) for item in result.items] == [
            (StaleWorktreeCategory.ORPHANED_DIRECTORY, PruneAction.PRUNED, paths.worktrees_dir / "orphan_1")
        ]
        assert not orphan_dir.exists()
