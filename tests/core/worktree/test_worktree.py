"""Contract tests for the Worktree entrypoint's delegation and prune failure reporting."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.lock import LockTimeoutError
from dovo.core.db import WorktreesRepository
from dovo.core.git.exceptions import GitCommandError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import (
    WorktreeApplyStatus,
    WorktreeCreateStatus,
    WorktreeDiffStatus,
    WorktreeListStatus,
    WorktreeShowStatus,
)
from dovo.core.worktree.services.detector import WorktreeDetector
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

        assert result.status == WorktreeCreateStatus.OK
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
        assert create_result.status == WorktreeCreateStatus.OK

        list_result = worktree.list()
        assert list_result.status == WorktreeListStatus.OK
        assert any(row.id == "dovo_entrypoint_create" for row in list_result.worktrees)

        show_result = worktree.show("dovo_entrypoint_create")
        assert show_result.status == WorktreeShowStatus.OK
        assert show_result.worktree is not None
        assert show_result.worktree.id == "dovo_entrypoint_create"

    def test_delete_reflects_the_same_worktree_created_through_the_entrypoint(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        worktree.create(session_id="dovo_entrypoint_delete")

        delete_result = worktree.delete("dovo_entrypoint_delete")

        assert delete_result.worktree_id == "dovo_entrypoint_delete"
        assert delete_result.worktree is not None


class WorktreeCleanupDelegationTests:
    def test_cleanup_removes_the_worktree_dovo_directory(self, worktree_workspace_paths: WorkspacePaths) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        create_result = worktree.create(session_id="dovo_entrypoint_cleanup")
        assert create_result.session is not None

        warnings = worktree.cleanup(create_result.session)

        assert warnings == []
        assert not create_result.session.worktree_path.exists()


class WorktreeDiffApplyDelegationTests:
    def test_diff_on_freshly_created_worktree_reports_empty_diff(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        create_result = worktree.create(session_id="dovo_entrypoint_diff")
        assert create_result.status == WorktreeCreateStatus.OK

        diff_result = worktree.diff("dovo_entrypoint_diff")

        assert diff_result.status == WorktreeDiffStatus.EMPTY_DIFF

    def test_apply_on_unknown_worktree_id_returns_not_found(self, worktree_workspace_paths: WorkspacePaths) -> None:
        worktree = Worktree(worktree_workspace_paths, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        result = worktree.apply("missing_worktree")

        assert result.status == WorktreeApplyStatus.NOT_FOUND


def _raise_type_error(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a call that fails with an unexpected TypeError("boom")."""
    raise TypeError("boom")


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
        """Return status None, error_code GIT_FAILED, errors == ["Git execution failed ('git worktree list'): fatal error"], items == [], diagnostics == [] and ok False, and leave <DOVO_HOME>/logs absent, when git worktree list raises GitCommandError(["worktree", "list"], 1, "", "fatal error")."""
        failure = GitCommandError(["worktree", "list"], 1, "", "fatal error")
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
