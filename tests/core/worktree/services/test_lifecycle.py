"""Integration tests for worktree lifecycle behavior."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.clock import Clock
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.models import Remediation
from dovo.common.session_id import SessionIdFactory
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitError, git_command_error
from dovo.core.git.models import GitProcessOutput
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.models import WorktreeCleanupResult
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.worktree import Worktree
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id
from tests.harness.workspace_paths import initialized_workspace_paths


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create an initialized Git workspace with a worktree database."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


def _paths(workspace: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for workspace, reflecting its current project.json."""
    return initialized_workspace_paths(workspace)


class WorktreeLifecycleStorageIndependenceTests:
    def test_create_makes_no_run_symlink_and_no_session_directory(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )

        result = worktree.create(session_id="dovo_independent")

        assert result.ok
        assert result.session is not None
        assert not (result.session.worktree_path / ".dovo" / "run").exists()
        assert not (result.session.worktree_path / ".dovo" / "run").is_symlink()
        assert not paths.session_dir("dovo_independent").exists()

    def test_cleanup_of_worktree_containing_run_symlink_leaves_link_target_contents_intact(
        self, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        create_result = worktree.create(session_id="dovo_linked_cleanup")
        assert create_result.session is not None
        session = create_result.session
        external_dir = tmp_path / "external-session"
        external_dir.mkdir()
        sentinel_path = external_dir / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        link_path = session.worktree_path / ".dovo" / "run"
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(external_dir, target_is_directory=True)

        result = worktree.cleanup(session)

        assert result == WorktreeCleanupResult(worktree_id="dovo_linked_cleanup")
        assert not session.worktree_path.exists()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"


class WorktreeLifecycleCapacityTests:
    def test_create_at_capacity_returns_capacity_exceeded_without_creating_worktree(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        for i in range(3):
            result = worktree.create(session_id=f"dovo_cap_{i}")
            assert result.ok

        result = worktree.create(session_id="dovo_cap_overflow")

        assert result.error_code == "WORKTREE_CAPACITY_EXCEEDED"
        assert not (worktree_workspace / ".dovo" / "worktrees" / "dovo_cap_overflow").exists()
        assert "dovo/dovo_cap_overflow" not in GitRunner().list_branches(worktree_workspace)


class WorktreeLifecycleInjectionTests:
    def test_create_without_session_id_uses_new_id_for_id_path_and_branch(self, worktree_workspace: Path) -> None:
        """Call new_id once with "dovo" and return session dovo_0000abcd at worktrees_dir/dovo_0000abcd on dovo/dovo_0000abcd."""
        paths = _paths(worktree_workspace)
        kinds: list[str] = []

        def recording_new_id(kind: str) -> str:
            kinds.append(kind)
            return fixed_new_id(kind)

        worktree = _worktree(worktree_workspace, new_id=recording_new_id)

        result = worktree.create()

        assert result.ok
        assert result.session is not None
        assert kinds == ["dovo"]
        assert result.session.session_id == "dovo_0000abcd"
        assert result.session.worktree_path == paths.worktrees_dir.resolve() / "dovo_0000abcd"
        assert result.session.target_branch == "dovo/dovo_0000abcd"

    def test_create_with_session_id_never_calls_new_id(self, worktree_workspace: Path) -> None:
        """Create session dovo_explicit without calling new_id."""
        kinds: list[str] = []

        def recording_new_id(kind: str) -> str:
            kinds.append(kind)
            return fixed_new_id(kind)

        worktree = _worktree(worktree_workspace, new_id=recording_new_id)

        result = worktree.create(session_id="dovo_explicit")

        assert result.ok
        assert result.session is not None
        assert result.session.session_id == "dovo_explicit"
        assert kinds == []

    def test_create_sets_created_at_from_one_clock_call(self, worktree_workspace: Path) -> None:
        """Call clock exactly once and set created_at to "2026-01-02T03:04:05+00:00"."""
        readings: list[datetime] = []

        def recording_clock() -> datetime:
            readings.append(fixed_clock())
            return readings[-1]

        worktree = _worktree(worktree_workspace, clock=recording_clock)

        result = worktree.create(session_id="dovo_clocked")

        assert result.session is not None
        assert result.session.created_at == "2026-01-02T03:04:05+00:00"
        assert len(readings) == 1

    def test_create_at_capacity_calls_neither_new_id_nor_clock(self, worktree_workspace: Path) -> None:
        """Return capacity_exceeded for create() at max_active_worktrees with zero new_id and clock calls after the fill."""
        calls: list[str] = []

        def recording_clock() -> datetime:
            calls.append("clock")
            return fixed_clock()

        def recording_new_id(kind: str) -> str:
            calls.append("new_id")
            return fixed_new_id(kind)

        worktree = _worktree(worktree_workspace, clock=recording_clock, new_id=recording_new_id)
        for i in range(3):
            assert worktree.create(session_id=f"dovo_fill_{i}").ok
        calls.clear()

        result = worktree.create()

        assert result.error_code == "WORKTREE_CAPACITY_EXCEEDED"
        assert calls == []


class WorktreeLifecycleCleanupTests:
    def test_cleanup_removes_dovo_directory_and_deletes_temporary_branch(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        create_result = worktree.create(session_id="dovo_cleanup_branch")
        assert create_result.session is not None
        session = create_result.session
        assert "dovo/dovo_cleanup_branch" in GitRunner().list_branches(worktree_workspace)

        result = worktree.cleanup(session)

        assert result == WorktreeCleanupResult(worktree_id="dovo_cleanup_branch")
        assert not session.worktree_path.exists()
        assert "dovo/dovo_cleanup_branch" not in GitRunner().list_branches(worktree_workspace)

    def test_cleanup_of_already_removed_worktree_is_idempotent_and_warning_free(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        create_result = worktree.create(session_id="dovo_cleanup_twice")
        assert create_result.session is not None
        session = create_result.session

        first = worktree.cleanup(session)
        second = worktree.cleanup(session)

        assert first == WorktreeCleanupResult(worktree_id="dovo_cleanup_twice")
        assert second == WorktreeCleanupResult(worktree_id="dovo_cleanup_twice")


class WorktreeLifecycleDiscardPartialTests:
    def test_discard_partial_removes_dovo_directory_and_branch(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths,
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        worktree_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_discard"
        temp_branch = "dovo/dovo_discard"
        GitRunner().worktree_add(worktree_workspace, worktree_path, temp_branch, "HEAD")
        assert worktree_path.is_dir()

        lifecycle.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)

        assert not worktree_path.exists()
        assert temp_branch not in GitRunner().list_branches(worktree_workspace)

    def test_discard_partial_on_nonexistent_branch_does_not_raise(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths,
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )

        lifecycle.discard_partial(
            worktree_workspace / ".dovo" / "worktrees" / "never-existed",
            "dovo/never-existed",
            created_dir=True,
            created_branch=True,
        )

    @pytest.mark.parametrize(
        ("created_dir", "created_branch"),
        [
            pytest.param(False, False, id="neither_created"),
            pytest.param(True, False, id="only_dir_created"),
            pytest.param(False, True, id="only_branch_created"),
        ],
    )
    def test_discard_partial_removes_only_flagged_resources(
        self, worktree_workspace: Path, created_dir: bool, created_branch: bool
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths,
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        worktree_path = paths.worktrees_dir / "dovo_flagged"
        worktree_path.mkdir(parents=True)
        sentinel = worktree_path / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        temp_branch = "dovo/dovo_flagged"
        GitRunner().run(["branch", temp_branch], path=worktree_workspace)

        lifecycle.discard_partial(worktree_path, temp_branch, created_dir=created_dir, created_branch=created_branch)

        assert sentinel.exists() is not created_dir
        assert GitRunner().branch_exists(worktree_workspace, temp_branch) is not created_branch


class WorktreeLifecycleCollisionTests:
    def test_create_with_existing_worktree_id_leaves_files_branch_tip_and_worktree_list_unchanged(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktree = Worktree(paths, db=repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        first = worktree.create(session_id="dovo_keep")
        assert first.session is not None
        kept_file = first.session.worktree_path / "kept.txt"
        kept_file.write_text("work in progress\n", encoding="utf-8")
        tip_before = GitRunner().rev_parse(worktree_workspace, rev="dovo/dovo_keep")
        list_before = GitRunner().run(["worktree", "list", "--porcelain"], path=worktree_workspace)

        result = worktree.create(session_id="dovo_keep")

        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.errors == ["Worktree 'dovo_keep' already exists."]
        assert result.remediations == [
            Remediation(
                message="Inspect it with 'dovo worktree show dovo_keep'", command="dovo worktree show dovo_keep"
            ),
            Remediation(
                message="Remove it with 'dovo worktree delete dovo_keep'", command="dovo worktree delete dovo_keep"
            ),
        ]
        assert kept_file.read_text(encoding="utf-8") == "work in progress\n"
        assert GitRunner().rev_parse(worktree_workspace, rev="dovo/dovo_keep") == tip_before
        assert GitRunner().run(["worktree", "list", "--porcelain"], path=worktree_workspace) == list_before
        assert [record.id for record in repo.list()] == ["dovo_keep"]

    def test_create_with_only_directory_returns_already_exists_and_keeps_it(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktree = Worktree(paths, db=repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        worktree_path = paths.worktrees_dir / "dovo_half"
        worktree_path.mkdir(parents=True)
        sentinel = worktree_path / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")

        result = worktree.create(session_id="dovo_half")

        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.remediations == [
            Remediation(message=f"Path '{worktree_path}' is not tracked by Dovo; move or remove it, then retry")
        ]
        assert sentinel.read_text(encoding="utf-8") == "keep\n"
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_half") is False
        assert repo.list() == []

    def test_create_over_cleaned_row_with_leftover_directory_advises_manual_removal(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktree = Worktree(paths, db=repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        worktree_path = paths.worktrees_dir / "dovo_stale"
        worktree_path.mkdir(parents=True)
        repo.create(
            id="dovo_stale",
            name=None,
            branch_name="dovo/dovo_stale",
            base_commit=GitRunner().rev_parse(worktree_workspace, rev="HEAD"),
            worktree_path=worktree_path,
        )
        repo.update_status("dovo_stale", WorktreeStatus.CLEANED)

        result = worktree.create(session_id="dovo_stale")

        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.remediations == [
            Remediation(message=f"Path '{worktree_path}' is not tracked by Dovo; move or remove it, then retry")
        ]
        assert worktree_path.is_dir()

    def test_create_with_only_branch_returns_already_exists_and_keeps_it(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        worktree = Worktree(paths, db=repo, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)
        GitRunner().run(["branch", "dovo/dovo_half"], path=worktree_workspace)
        tip_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")

        result = worktree.create(session_id="dovo_half")

        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.remediations == [
            Remediation(
                message=(
                    "Branch 'dovo/dovo_half' is not tracked by Dovo; "
                    "delete it with 'git branch -D dovo/dovo_half' if you no longer need it, then retry"
                ),
                command="git branch -D dovo/dovo_half",
            )
        ]
        assert GitRunner().rev_parse(worktree_workspace, rev="dovo/dovo_half") == tip_before
        assert (paths.worktrees_dir / "dovo_half").exists() is False
        assert repo.list() == []

    def test_create_with_dangling_symlink_at_id_returns_already_exists_and_leaves_link_target_absent(
        self, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        paths.worktrees_dir.mkdir(parents=True, exist_ok=True)
        link_target = tmp_path / "outside-worktrees"
        link_path = paths.worktrees_dir / "dovo_link"
        link_path.symlink_to(link_target, target_is_directory=True)

        result = worktree.create(session_id="dovo_link")

        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert link_path.is_symlink() is True
        assert link_target.exists() is False
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_link") is False

    @pytest.mark.parametrize(
        ("probe_error", "expected_code", "expected_error_prefix"),
        [
            pytest.param(
                GitError(ErrorCode.GIT_TIMEOUT, "show-ref timed out"),
                ErrorCode.GIT_TIMEOUT,
                "Git worktree operation timed out: show-ref timed out",
                id="probe_timeout",
            ),
            pytest.param(
                git_command_error(args=["show-ref"], returncode=128, stdout="", stderr="fatal"),
                ErrorCode.GIT_FAILED,
                "Git worktree operation failed: ",
                id="probe_fatal_exit_128",
            ),
        ],
    )
    def test_create_when_branch_probe_fails_returns_error_status_and_creates_nothing(
        self,
        worktree_workspace: Path,
        probe_error: Exception,
        expected_code: ErrorCode,
        expected_error_prefix: str,
    ) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=FakeGitRunner({("show-ref",): probe_error}),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )

        result = worktree.create(session_id="dovo_probefail")

        assert result.error_code == expected_code
        assert len(result.errors) == 1
        assert result.errors[0].startswith(expected_error_prefix)
        assert (paths.worktrees_dir / "dovo_probefail").exists() is False
        assert "dovo/dovo_probefail" not in GitRunner().list_branches(worktree_workspace)

    def test_create_when_path_check_raises_oserror_returns_create_failed_and_records_cause(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return WORKTREE_CREATE_FAILED with the path-check message and remediation, and one error record for the PermissionError in diagnostics."""
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        real_exists = Path.exists

        def denied_exists(self: Path, *, follow_symlinks: bool = True) -> bool:
            if self.name == "dovo_denied":
                raise PermissionError("permission denied")
            return real_exists(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "exists", denied_exists)

        result = worktree.create(session_id="dovo_denied")

        assert result.error_code == "WORKTREE_CREATE_FAILED"
        assert result.errors == ["Worktree path check failed: permission denied"]
        assert result.remediations == [
            Remediation(message=f"Check that '{paths.worktrees_dir}' is readable, then retry")
        ]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["exception_type"] == "PermissionError"
        assert "dovo/dovo_denied" not in GitRunner().list_branches(worktree_workspace)

    def test_create_when_add_fails_and_branch_probe_raises_keeps_branch(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)

        class _AddCreatesBranchThenFails(FakeGitRunner):
            added = False

            def exec(
                self,
                args: Sequence[str],
                path: Path,
                *,
                input_text: str | None = None,
                env: Mapping[str, str] | None = None,
            ) -> GitProcessOutput:
                if tuple(args[:2]) == ("worktree", "add"):
                    _, _, _, branch, _, base_ref = args
                    super().exec(["branch", branch, base_ref], path)
                    self.added = True
                    raise git_command_error(args=["worktree", "add"], returncode=128, stdout="", stderr="boom")
                if self.added and args[0] == "show-ref":
                    raise git_command_error(args=["show-ref"], returncode=128, stdout="", stderr="fatal")
                return super().exec(args, path, input_text=input_text, env=env)

        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=_AddCreatesBranchThenFails(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )

        result = worktree.create(session_id="dovo_keepbranch")

        assert result.error_code == "GIT_FAILED"
        assert "dovo/dovo_keepbranch" in GitRunner().list_branches(worktree_workspace)

    def test_create_with_bad_base_ref_on_fresh_id_returns_git_failed_and_leaves_nothing(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )
        sibling = worktree.create(session_id="dovo_sibling")
        assert sibling.session is not None

        result = worktree.create(session_id="dovo_fresh", base_ref="does-not-exist")

        assert result.error_code == "GIT_FAILED"
        assert (paths.worktrees_dir / "dovo_fresh").exists() is False
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_fresh") is False
        assert sibling.session.worktree_path.is_dir()
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_sibling") is True

    def test_create_when_rev_parse_fails_after_add_removes_created_worktree_and_branch(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        failure = git_command_error(args=["rev-parse", "HEAD"], returncode=128, stdout="", stderr="boom")
        worktree = Worktree(
            paths,
            db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=FakeGitRunner({("rev-parse", "HEAD"): failure}),
            clock=fixed_clock,
            new_id=fixed_new_id,
        )

        result = worktree.create(session_id="dovo_revfail")

        assert result.error_code == "GIT_FAILED"
        assert (paths.worktrees_dir / "dovo_revfail").exists() is False
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_revfail") is False


def _lifecycle(
    workspace: Path,
    git: GitRunner | None = None,
    *,
    clock: Clock = fixed_clock,
    new_id: SessionIdFactory = fixed_new_id,
) -> WorktreeLifecycle:
    """Build a WorktreeLifecycle over workspace's paths and worktree database, running git through git or a real GitRunner."""
    paths = _paths(workspace)
    return WorktreeLifecycle(
        paths,
        WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
        git=git if git is not None else GitRunner(),
        clock=clock,
        new_id=new_id,
    )


def _worktree(
    workspace: Path,
    git: GitRunner | None = None,
    *,
    clock: Clock = fixed_clock,
    new_id: SessionIdFactory = fixed_new_id,
) -> Worktree:
    """Build a Worktree over workspace's paths and worktree database, running git through git or a real GitRunner."""
    paths = _paths(workspace)
    return Worktree(
        paths,
        db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
        git=git if git is not None else GitRunner(),
        clock=clock,
        new_id=new_id,
    )


def _raise_boom(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a repository call that fails with an unexpected RuntimeError("boom")."""
    raise RuntimeError("boom")


_GIT_KEYS: dict[str, tuple[str, ...]] = {
    "branch_exists": ("show-ref",),
    "worktree_add": ("worktree", "add"),
    "worktree_remove": ("worktree", "remove"),
    "worktree_prune": ("worktree", "prune"),
    "branch_delete": ("branch", "-D"),
    "rev_parse": ("rev-parse", "HEAD"),
}


_GIT_FAILED_REMEDIATION = Remediation(message="Ensure this directory is a Git repository with a valid base ref")
_GIT_TIMEOUT_REMEDIATION = Remediation(
    message="Check for git lock files, credential prompts, or stuck git processes, then retry"
)
_WIP_FAILED_REMEDIATION = Remediation(message="Resolve local conflicts and retry, or commit changes first")


class WorktreeCreateFailureCodeTests:
    @pytest.mark.parametrize(
        ("git_key", "git_error", "include_wip", "error_code", "message", "remediations"),
        [
            pytest.param(
                ("worktree", "add"),
                git_command_error(["worktree", "add"], 128, "", "fatal: bad ref"),
                False,
                ErrorCode.GIT_FAILED,
                "Git worktree operation failed: Git execution failed ('git worktree add'): fatal: bad ref",
                [_GIT_FAILED_REMEDIATION],
                id="add_fails",
            ),
            pytest.param(
                ("worktree", "add"),
                GitError(ErrorCode.GIT_TIMEOUT, "add timed out"),
                False,
                ErrorCode.GIT_TIMEOUT,
                "Git worktree operation timed out: add timed out",
                [_GIT_TIMEOUT_REMEDIATION],
                id="add_timeout",
            ),
            pytest.param(
                ("rev-parse", "HEAD"),
                git_command_error(["rev-parse", "HEAD"], 128, "", "boom"),
                False,
                ErrorCode.GIT_FAILED,
                "Git worktree operation failed: Git execution failed ('git rev-parse HEAD'): boom",
                [],
                id="rev_parse_fails",
            ),
            pytest.param(
                ("status",),
                GitError(ErrorCode.GIT_WORKDIR_MISSING, "status failed"),
                True,
                ErrorCode.GIT_WORKDIR_MISSING,
                "Failed to overlay uncommitted WIP into worktree: status failed",
                [_WIP_FAILED_REMEDIATION],
                id="wip_git_fails",
            ),
        ],
    )
    def test_create_failure_reports_code_message_and_remediations(
        self,
        worktree_workspace: Path,
        frozen_diagnostic_clock: Path,
        git_key: tuple[str, ...],
        git_error: Exception,
        include_wip: bool,
        error_code: ErrorCode,
        message: str,
        remediations: list[Remediation],
    ) -> None:
        """Return the given error_code, errors == [message], remediations, diagnostics == [], no worktree directory or dovo/<id> branch, and leave <DOVO_HOME>/logs absent."""
        worktree = _worktree(worktree_workspace, FakeGitRunner({git_key: git_error}))

        result = worktree.create(session_id="dovo_code", include_wip=include_wip)

        assert result.error_code == error_code
        assert result.errors == [message]
        assert result.remediations == remediations
        assert result.diagnostics == []
        assert result.session is None
        assert not (_paths(worktree_workspace).worktrees_dir / "dovo_code").exists()
        assert not GitRunner().branch_exists(worktree_workspace, "dovo/dovo_code")
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_create_git_error_with_oserror_cause_records_it(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Return error_code GIT_WORKDIR_MISSING and one diagnostics entry whose event has exception_type FileNotFoundError when git worktree add raises GitError(GIT_WORKDIR_MISSING) from FileNotFoundError."""
        try:
            raise FileNotFoundError("cwd vanished")
        except FileNotFoundError as cause:
            failure = GitError(ErrorCode.GIT_WORKDIR_MISSING, "working directory does not exist")
            failure.__cause__ = cause
        worktree = _worktree(worktree_workspace, FakeGitRunner({("worktree", "add"): failure}))

        result = worktree.create(session_id="dovo_vanished")

        assert result.error_code == ErrorCode.GIT_WORKDIR_MISSING
        assert result.errors == ["Git worktree operation failed: working directory does not exist"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["exception_type"] == "FileNotFoundError"


class WorktreeLifecycleDiagnosticLogTests:
    @pytest.mark.parametrize("target", ["branch_exists", "worktree_add", "rev_parse"])
    def test_create_unexpected_failure_reports_internal_error_after_discard(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, target: str
    ) -> None:
        """Return INTERNAL_ERROR, errors == ["Unexpected error during worktree.create: boom"] and one diagnostics entry for an error event with operation worktree.create and context {worktree_id, path}, leaving no directory or branch."""
        worktree = _worktree(worktree_workspace, FakeGitRunner({_GIT_KEYS[target]: RuntimeError("boom")}))

        result = worktree.create(session_id="dovo_boom")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.create: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["context"] == {"worktree_id": "dovo_boom", "path": str(_paths(worktree_workspace).root_dir)}
        assert event["exception_type"] == "RuntimeError"
        assert not (_paths(worktree_workspace).worktrees_dir / "dovo_boom").exists()
        assert not GitRunner().branch_exists(worktree_workspace, "dovo/dovo_boom")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_wip_copy_failure_records_error_in_diagnostics(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Return WORKTREE_WIP_COPY_FAILED with an unhinted "Failed to overlay uncommitted WIP into worktree: " error and one diagnostics entry for a PermissionError error event, when create(include_wip=True) copies an untracked WIP file chmod 000."""
        worktree = _worktree(worktree_workspace)
        unreadable = worktree_workspace / "secret.txt"
        unreadable.write_text("wip\n", encoding="utf-8")
        unreadable.chmod(0o000)
        try:
            result = worktree.create(session_id="dovo_wip", include_wip=True)
        finally:
            unreadable.chmod(0o644)

        assert result.error_code == ErrorCode.WORKTREE_WIP_COPY_FAILED
        assert len(result.errors) == 1
        assert result.errors[0].startswith("Failed to overlay uncommitted WIP into worktree: ")
        assert "Details logged:" not in result.errors[0]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["exception_type"] == "PermissionError"

    def test_wip_domain_error_writes_no_record(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return WORKTREE_MISSING with the unhinted error, empty diagnostics, and leave <DOVO_HOME>/logs absent when apply_wip_to_worktree raises WorktreeError("worktree path does not exist: x") with no cause."""
        worktree = _worktree(worktree_workspace)

        def missing_worktree(*, git: GitRunner, source_root: Path, worktree_path: Path) -> list[str]:
            raise WorktreeError(ErrorCode.WORKTREE_MISSING, "worktree path does not exist: x")

        monkeypatch.setattr("dovo.core.worktree.services.lifecycle.apply_wip_to_worktree", missing_worktree)

        result = worktree.create(session_id="dovo_wipdomain", include_wip=True)

        assert result.error_code == ErrorCode.WORKTREE_MISSING
        assert result.errors == ["Failed to overlay uncommitted WIP into worktree: worktree path does not exist: x"]
        assert result.diagnostics == []
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_persist_failure_records_error_in_diagnostics(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return ok with warnings == ["Failed to persist worktree metadata to the local database: boom"] and diagnostics == [diagnostic_record(...)] when WorktreesRepository.create raises RuntimeError("boom")."""
        worktree = _worktree(worktree_workspace)
        monkeypatch.setattr(WorktreesRepository, "create", _raise_boom)

        result = worktree.create(session_id="dovo_persist")

        assert result.ok
        assert result.warnings == ["Failed to persist worktree metadata to the local database: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert result.session is not None
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["context"] == {
            "worktree_id": "dovo_persist",
            "branch": "dovo/dovo_persist",
            "path": str(result.session.worktree_path),
        }

    @pytest.mark.parametrize("target", ["worktree_remove", "branch_delete", "worktree_prune"])
    def test_discard_partial_failure_records_debug_without_raising(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, target: str
    ) -> None:
        """Return None from discard_partial(created_dir=True, created_branch=True) when the git command behind <target> raises RuntimeError, and write one debug record for worktree.create."""
        fake = FakeGitRunner()
        lifecycle = _lifecycle(worktree_workspace, fake)
        worktree_path = lifecycle.worktree_base_dir / "dovo_partial"
        GitRunner().worktree_add(worktree_workspace, worktree_path, "dovo/dovo_partial", "HEAD")
        fake.responses[_GIT_KEYS[target]] = RuntimeError("boom")

        outcome = lifecycle.discard_partial(worktree_path, "dovo/dovo_partial", created_dir=True, created_branch=True)

        assert outcome is None
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.create"
        assert event["context"] == {"branch": "dovo/dovo_partial", "path": str(worktree_path)}
        assert event["exception_type"] == "RuntimeError"

    def test_branch_probe_failure_records_debug_and_keeps_git_failed(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Return GIT_FAILED with an unhinted error and empty diagnostics when worktree_add raises a GIT_FAILED GitError and the follow-up branch probe raises RuntimeError, and write one debug record for worktree.create."""

        class _SecondProbeFails(FakeGitRunner):
            probes = 0

            def exec(
                self,
                args: Sequence[str],
                path: Path,
                *,
                input_text: str | None = None,
                env: Mapping[str, str] | None = None,
            ) -> GitProcessOutput:
                if args[0] == "show-ref":
                    self.probes += 1
                    if self.probes == 1:
                        return GitProcessOutput(1, "", "")
                    raise RuntimeError("boom")
                return super().exec(args, path, input_text=input_text, env=env)

        failure = git_command_error(args=["worktree", "add"], returncode=128, stdout="", stderr="fatal")
        worktree = _worktree(worktree_workspace, _SecondProbeFails({("worktree", "add"): failure}))

        result = worktree.create(session_id="dovo_probe")

        assert result.error_code == ErrorCode.GIT_FAILED
        assert len(result.errors) == 1
        assert result.errors[0].startswith("Git worktree operation failed: ")
        assert "Details logged:" not in result.errors[0]
        assert result.diagnostics == []
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.create"
        assert event["context"] == {"branch": "dovo/dovo_probe"}
        assert event["exception_type"] == "RuntimeError"

    @pytest.mark.parametrize(
        ("target", "prefix"),
        [
            pytest.param("branch_delete", "Failed to delete branch 'dovo/dovo_clean': boom", id="branch_delete"),
            pytest.param(
                "update_status",
                "Failed to update database status to 'cleaned' for worktree 'dovo_clean': boom",
                id="update_status",
            ),
            pytest.param("worktree_prune", "Failed to prune git worktrees: boom", id="worktree_prune"),
        ],
    )
    def test_cleanup_step_failure_reports_warning_and_record(
        self,
        worktree_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
        prefix: str,
    ) -> None:
        """Return ok, warnings == [prefix] with no hint, diagnostics == [diagnostic_record(frozen_diagnostic_clock)] from Worktree.cleanup(), and one error event with operation worktree.cleanup and today's context when the step raises RuntimeError("boom")."""
        fake = FakeGitRunner()
        worktree = _worktree(worktree_workspace, fake)
        session = worktree.create(session_id="dovo_clean").session
        assert session is not None
        if target == "update_status":
            monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        else:
            fake.responses[_GIT_KEYS[target]] = RuntimeError("boom")

        result = worktree.cleanup(session)

        assert result.ok
        assert result.warnings == [prefix]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.cleanup"
        if target == "branch_delete":
            assert event["context"] == {"branch": "dovo/dovo_clean"}
        else:
            assert event["context"] == {
                "worktree_id": "dovo_clean",
                "branch": "dovo/dovo_clean",
                "path": str(session.worktree_path),
            }

    @pytest.mark.parametrize(
        ("target", "prefix"),
        [
            pytest.param("branch_delete", "Failed to delete branch 'dovo/dovo_clean': ", id="branch_delete"),
            pytest.param("worktree_prune", "Failed to prune git worktrees: ", id="worktree_prune"),
        ],
    )
    @pytest.mark.parametrize("code", [ErrorCode.GIT_TIMEOUT, ErrorCode.GIT_WORKDIR_MISSING])
    def test_cleanup_reports_git_codes_it_does_not_ignore(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, target: str, prefix: str, code: ErrorCode
    ) -> None:
        """Return ok with warnings == [prefix + "boom"], diagnostics == [] and no <DOVO_HOME>/logs when the step raises GitError(code, "boom") with no cause."""
        fake = FakeGitRunner()
        worktree = _worktree(worktree_workspace, fake)
        session = worktree.create(session_id="dovo_clean").session
        assert session is not None
        fake.responses[_GIT_KEYS[target]] = GitError(code, "boom")

        result = worktree.cleanup(session)

        assert result.ok
        assert result.warnings == [f"{prefix}boom"]
        assert result.diagnostics == []
        assert not resolve_global_paths(None).logs_dir.exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    @pytest.mark.parametrize(
        "rmtree_fails", [pytest.param(False, id="rmtree_succeeds"), pytest.param(True, id="rmtree_fails")]
    )
    def test_cleanup_directory_fallback_records_by_outcome(
        self,
        worktree_workspace: Path,
        frozen_diagnostic_clock: Path,
        rmtree_fails: bool,
    ) -> None:
        """With git worktree remove raising RuntimeError, return no directory warning, empty diagnostics and one debug record when rmtree succeeds, or an unhinted directory warning and one error record in diagnostics when worktrees_dir is read-only."""
        fake = FakeGitRunner()
        worktree = _worktree(worktree_workspace, fake)
        worktrees_dir = _paths(worktree_workspace).worktrees_dir
        session = worktree.create(session_id="dovo_fallback").session
        assert session is not None
        fake.responses[("worktree", "remove")] = RuntimeError("boom")
        if rmtree_fails:
            worktrees_dir.chmod(0o555)
        try:
            result = worktree.cleanup(session)
        finally:
            worktrees_dir.chmod(0o755)

        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "worktree.cleanup"
        assert event["context"] == {"path": str(session.worktree_path)}
        dir_warnings = [
            warning for warning in result.warnings if warning.startswith("Failed to remove worktree directory")
        ]
        if rmtree_fails:
            assert len(dir_warnings) == 1
            assert dir_warnings[0].startswith(
                f"Failed to remove worktree directory at '{session.worktree_path}': boom; fallback removal failed: "
            )
            assert "Details logged:" not in dir_warnings[0]
            assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
            assert event["level"] == "error"
            assert event["exception_type"] == "PermissionError"
        else:
            assert dir_warnings == []
            assert result.diagnostics == []
            assert not session.worktree_path.exists()
            assert event["level"] == "debug"
            assert event["exception_type"] == "RuntimeError"

    def test_repeated_cleanup_writes_no_record(self, worktree_workspace: Path, frozen_diagnostic_clock: Path) -> None:
        """Return WorktreeCleanupResult(worktree_id="dovo_twice") from a second cleanup() of the same worktree and leave <DOVO_HOME>/logs absent."""
        worktree = _worktree(worktree_workspace)
        session = worktree.create(session_id="dovo_twice").session
        assert session is not None
        worktree.cleanup(session)

        result = worktree.cleanup(session)

        assert result == WorktreeCleanupResult(worktree_id="dovo_twice")
        assert not resolve_global_paths(None).logs_dir.exists()
