"""Contract tests for the Worktree entrypoint's delegation and prune failure reporting."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation
from dovo.core.config import ConfigLoadError
from dovo.core.db import WorktreeRecord, WorktreesRepository
from dovo.core.git.exceptions import git_command_error
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeCleanupResult
from dovo.core.worktree.services.detector import WorktreeDetector
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.worktree import Worktree
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id


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


class WorktreeEntrypointInjectionTests:
    def test_create_returns_literal_id_path_branch_and_created_at(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return the issue's four literal values from Worktree(clock=fixed_clock, new_id=fixed_new_id).create()."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.create()

        assert result.ok
        assert result.session is not None
        assert result.session.session_id == "dovo_0000abcd"
        assert result.session.worktree_path == worktree_workspace_paths.worktrees_dir.resolve() / "dovo_0000abcd"
        assert result.session.target_branch == "dovo/dovo_0000abcd"
        assert result.session.created_at == "2026-01-02T03:04:05+00:00"


class WorktreeCreateListShowDeleteDelegationTests:
    def test_create_persists_a_worktree_that_list_and_show_can_find(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        create_result = worktree.create(session_id="dovo_entrypoint_create")
        assert create_result.ok

        list_result = worktree.list()
        assert list_result.ok
        assert any(row.id == "dovo_entrypoint_create" for row in list_result.worktrees)

        show_result = worktree.show("dovo_entrypoint_create")
        assert show_result.ok
        assert show_result.worktree is not None
        assert show_result.worktree.id == "dovo_entrypoint_create"

    def test_delete_reflects_the_same_worktree_created_through_the_entrypoint(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_entrypoint_delete")
        assert created.session is not None

        delete_result = worktree.delete("dovo_entrypoint_delete")

        assert delete_result.worktree_id == "dovo_entrypoint_delete"
        assert delete_result.worktree is not None
        assert delete_result.deleted is True
        assert not created.session.worktree_path.exists()


class WorktreeCleanupDelegationTests:
    def test_cleanup_removes_the_worktree_dovo_directory(self, worktree_workspace_paths: WorkspacePaths) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        create_result = worktree.create(session_id="dovo_entrypoint_cleanup")
        assert create_result.session is not None

        result = worktree.cleanup(create_result.session)

        assert result == WorktreeCleanupResult(worktree_id="dovo_entrypoint_cleanup")
        assert not create_result.session.worktree_path.exists()


def _raise_type_error(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a call that fails with an unexpected TypeError("boom")."""
    raise TypeError("boom")


def _lock_timeout(self: object) -> NoReturn:
    """Stand in for WorkspaceLock.__enter__ when another process holds the lock too long."""
    raise LockTimeoutError("Timed out waiting for workspace lock")


class WorktreeCreateEntrypointTests:
    def test_create_unexpected_failure_returns_internal_error_with_one_diagnostic(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return error_code INTERNAL_ERROR, errors == ["Unexpected error during worktree.create: boom"], session None, diagnostics == [diagnostic_record(frozen_diagnostic_clock)] and one error event with operation worktree.create when git worktree add raises TypeError("boom"); leave no worktree directory and no dovo/<id> branch."""
        git = FakeGitRunner({("worktree", "add"): TypeError("boom")})
        worktree = Worktree(worktree_workspace_paths, git=git, clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.create(session_id="dovo_typeerror")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.create: boom"]
        assert result.session is None
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["exception_type"] == "TypeError"
        assert not (worktree_workspace_paths.worktrees_dir / "dovo_typeerror").exists()
        assert not GitRunner().branch_exists(worktree_workspace, "dovo/dovo_typeerror")

    def test_create_unexpected_failure_with_unwritable_log_has_empty_diagnostics(
        self, worktree_workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_code INTERNAL_ERROR with the unexpected-error message and diagnostics == [] when record_exception returns None."""
        monkeypatch.setattr("dovo.common.exceptions.record_exception", lambda *args, **kwargs: None)
        git = FakeGitRunner({("worktree", "add"): TypeError("boom")})
        worktree = Worktree(worktree_workspace_paths, git=git, clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.create(session_id="dovo_unlogged")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.create: boom"]
        assert result.diagnostics == []

    def test_create_config_load_error_propagates(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """Raise ConfigLoadError from Worktree.create() when config.json holds malformed JSON, writing no event file."""
        worktree_workspace_paths.config_file.write_text("{not json", encoding="utf-8")
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        with pytest.raises(ConfigLoadError):
            worktree.create(session_id="dovo_badconfig")

        assert not resolve_global_paths().logs_dir.exists()

    def test_create_lock_timeout_propagates(
        self, worktree_workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError from Worktree.create() when WorkspaceLock.__enter__ raises it, creating no worktree directory."""
        monkeypatch.setattr("dovo.core.worktree.worktree.WorkspaceLock.__enter__", _lock_timeout)
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        with pytest.raises(LockTimeoutError):
            worktree.create(session_id="dovo_locked")

        assert not (worktree_workspace_paths.worktrees_dir / "dovo_locked").exists()


class WorktreeCleanupEntrypointTests:
    def test_cleanup_unexpected_failure_returns_internal_error_with_one_diagnostic(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_code INTERNAL_ERROR, errors == ["Unexpected error during worktree.cleanup: boom"] and one diagnostics entry when WorktreeLifecycle.cleanup raises TypeError("boom")."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_cleanup_boom")
        assert created.session is not None
        monkeypatch.setattr(WorktreeLifecycle, "cleanup", _raise_type_error)

        result = worktree.cleanup(created.session)

        assert result.worktree_id == "dovo_cleanup_boom"
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.cleanup: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "worktree.cleanup"
        assert event["context"] == {
            "worktree_id": "dovo_cleanup_boom",
            "path": str(created.session.worktree_path),
        }

    def test_cleanup_lock_timeout_propagates(
        self, worktree_workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError from Worktree.cleanup() and leave the worktree directory in place."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_cleanup_locked")
        assert created.session is not None
        monkeypatch.setattr("dovo.core.worktree.worktree.WorkspaceLock.__enter__", _lock_timeout)

        with pytest.raises(LockTimeoutError):
            worktree.cleanup(created.session)

        assert created.session.worktree_path.is_dir()


class WorktreeApplyLockTests:
    def test_apply_lock_timeout_propagates(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError from Worktree.apply() and leave the main repository unchanged."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_apply_locked")
        assert created.session is not None
        (created.session.worktree_path / "feature.py").write_text("feature\n", encoding="utf-8")
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        status_before = GitRunner().status_porcelain(worktree_workspace)
        monkeypatch.setattr("dovo.core.worktree.worktree.WorkspaceLock.__enter__", _lock_timeout)

        with pytest.raises(LockTimeoutError):
            worktree.apply("dovo_apply_locked")

        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before
        assert GitRunner().status_porcelain(worktree_workspace) == status_before


class WorktreeDeleteEntrypointTests:
    def test_delete_asks_confirm_once_and_removes(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Call confirm once with the row, then return deleted True, cancelled False, already_cleaned False, error_code None, and remove the directory and branch."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_confirmed")
        assert created.session is not None
        asked: list[WorktreeRecord] = []

        def confirm(row: WorktreeRecord) -> bool:
            asked.append(row)
            return True

        result = worktree.delete("dovo_confirmed", confirm=confirm)

        assert [row.id for row in asked] == ["dovo_confirmed"]
        assert result.deleted is True
        assert result.cancelled is False
        assert result.already_cleaned is False
        assert result.error_code is None
        assert not created.session.worktree_path.exists()
        assert not GitRunner().branch_exists(worktree_workspace, "dovo/dovo_confirmed")

    def test_delete_declined_returns_cancelled_and_removes_nothing(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return cancelled True, deleted False, errors == [], error_code None and keep the directory and branch when confirm returns False."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_declined")
        assert created.session is not None

        result = worktree.delete("dovo_declined", confirm=lambda row: False)

        assert result.cancelled is True
        assert result.deleted is False
        assert result.errors == []
        assert result.error_code is None
        assert created.session.worktree_path.is_dir()
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_declined")

    def test_delete_unknown_id_reports_not_found_without_prompting(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return error_code WORKTREE_NOT_FOUND, errors == ["Worktree 'dovo_missing' not found."], the 'dovo worktree list' remediation, diagnostics == [], and never call confirm."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        asked: list[WorktreeRecord] = []

        result = worktree.delete("dovo_missing", confirm=lambda row: asked.append(row) is None)

        assert result.error_code == ErrorCode.WORKTREE_NOT_FOUND
        assert result.errors == ["Worktree 'dovo_missing' not found."]
        assert result.remediations == [
            Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")
        ]
        assert result.diagnostics == []
        assert asked == []

    def test_delete_cleaned_row_returns_already_cleaned_without_prompting(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return already_cleaned True, deleted False, error_code None, and never call confirm, for a row with status cleaned."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_cleaned")
        assert created.session is not None
        worktree.cleanup(created.session)
        asked: list[WorktreeRecord] = []

        result = worktree.delete("dovo_cleaned", confirm=lambda row: asked.append(row) is None)

        assert result.already_cleaned is True
        assert result.deleted is False
        assert result.error_code is None
        assert asked == []

    def test_delete_unexpected_failure_returns_internal_error_with_one_diagnostic(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_code INTERNAL_ERROR, errors == ["Unexpected error during worktree.delete: boom"], diagnostics == [diagnostic_record(...)] and one error event with operation worktree.delete when WorktreesRepository.get raises TypeError("boom")."""
        monkeypatch.setattr(WorktreesRepository, "get", _raise_type_error)
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.delete("dovo_any")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.delete: boom"]
        assert result.worktree is None
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.delete"
        assert event["context"] == {"worktree_id": "dovo_any"}

    def test_delete_lock_timeout_propagates_after_confirmation(
        self, worktree_workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError after confirm returned True, leaving the worktree directory in place."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_delete_locked")
        assert created.session is not None
        monkeypatch.setattr("dovo.core.worktree.worktree.WorkspaceLock.__enter__", _lock_timeout)
        asked: list[str] = []

        def confirm(row: WorktreeRecord) -> bool:
            asked.append(row.id)
            return True

        with pytest.raises(LockTimeoutError):
            worktree.delete("dovo_delete_locked", confirm=confirm)

        assert asked == ["dovo_delete_locked"]
        assert created.session.worktree_path.is_dir()


def _raise_database_locked(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a database write that fails with sqlite3.OperationalError("database is locked")."""
    raise sqlite3.OperationalError("database is locked")


class WorktreeShowListEntrypointTests:
    def test_show_unknown_id_reports_not_found_without_record(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return error_code WORKTREE_NOT_FOUND, errors == ["Worktree 'dovo_missing' not found."], the 'dovo worktree list' remediation and diagnostics == [], and leave <DOVO_HOME>/logs absent."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.show("dovo_missing")

        assert result.error_code == ErrorCode.WORKTREE_NOT_FOUND
        assert result.errors == ["Worktree 'dovo_missing' not found."]
        assert result.remediations == [
            Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")
        ]
        assert result.diagnostics == []
        assert not resolve_global_paths().logs_dir.exists()

    def test_show_unexpected_failure_returns_internal_error_with_one_diagnostic(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_code INTERNAL_ERROR, errors == ["Unexpected error during worktree.show: database is locked"], diagnostics == [diagnostic_record(frozen_diagnostic_clock)] and one error event with operation worktree.show and context {"worktree_id": <id>}, when WorktreesRepository.update_status raises sqlite3.OperationalError("database is locked") while reconciling a stale active row."""
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_stale_show")
        assert created.session is not None
        shutil.rmtree(created.session.worktree_path)
        monkeypatch.setattr(WorktreesRepository, "update_status", _raise_database_locked)

        result = worktree.show("dovo_stale_show")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.show: database is locked"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.show"
        assert event["context"] == {"worktree_id": "dovo_stale_show"}

    def test_list_unexpected_failure_returns_internal_error_with_one_diagnostic(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error_code INTERNAL_ERROR, errors == ["Unexpected error during worktree.list: database is locked"], worktrees == [], diagnostics == [diagnostic_record(frozen_diagnostic_clock)] and one error event with operation worktree.list, when WorktreesRepository.reconcile_stale_active raises sqlite3.OperationalError("database is locked")."""
        monkeypatch.setattr(WorktreesRepository, "reconcile_stale_active", _raise_database_locked)
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.list()

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.list: database is locked"]
        assert result.worktrees == []
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.list"


class WorktreePruneEntrypointTests:
    @pytest.mark.parametrize(
        "trigger",
        [
            pytest.param("detect", id="detect"),
            pytest.param("db_list", id="db_list"),
        ],
    )
    def test_prune_unexpected_failure_returns_internal_error_with_one_diagnostic(
        self,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        trigger: str,
    ) -> None:
        """Return status None, error_code INTERNAL_ERROR, errors == ["Unexpected error during worktree.prune: boom"], items == [], ok False, diagnostics == [diagnostic_record(frozen_diagnostic_clock)], and one error event with operation worktree.prune when detection raises TypeError("boom")."""
        if trigger == "detect":
            monkeypatch.setattr(WorktreeDetector, "detect", _raise_type_error)
        else:
            monkeypatch.setattr(WorktreesRepository, "list", _raise_type_error)
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.prune()

        assert result.status is None
        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.prune: boom"]
        assert result.items == []
        assert result.ok is False
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.prune"
        assert event["exception_type"] == "TypeError"

    def test_prune_reports_detection_git_error_by_its_code(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return status None, error_code GIT_FAILED, errors == ["Git execution failed ('git worktree list'): fatal error"], items == [], diagnostics == [] and ok False, and leave <DOVO_HOME>/logs absent, when git worktree list raises git_command_error(["worktree", "list"], 1, "", "fatal error")."""
        failure = git_command_error(["worktree", "list"], 1, "", "fatal error")
        git = FakeGitRunner({("worktree", "list"): failure})
        worktree = Worktree(worktree_workspace_paths, git=git, clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.prune()

        assert result.status is None
        assert result.error_code == ErrorCode.GIT_FAILED
        assert result.errors == ["Git execution failed ('git worktree list'): fatal error"]
        assert result.items == []
        assert result.diagnostics == []
        assert result.ok is False
        assert not resolve_global_paths().logs_dir.exists()

    def test_prune_lock_timeout_propagates_and_prunes_nothing(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise LockTimeoutError from Worktree.prune() when dovo.core.worktree.worktree.WorkspaceLock.__enter__ raises it, leaving a stale dovo/dovo_* branch in place."""
        git = GitRunner()
        subprocess.run(["git", "branch", "dovo/dovo_stale"], cwd=worktree_workspace, check=True, capture_output=True)

        def _locked(self: object) -> NoReturn:
            raise LockTimeoutError("Timed out waiting for workspace lock")

        monkeypatch.setattr("dovo.core.worktree.worktree.WorkspaceLock.__enter__", _locked)
        worktree = Worktree(worktree_workspace_paths, git=git, clock=fixed_clock, new_id=fixed_new_id)

        with pytest.raises(LockTimeoutError):
            worktree.prune()

        assert git.list_branches(worktree_workspace, pattern="dovo/dovo_*") == ["dovo/dovo_stale"]
