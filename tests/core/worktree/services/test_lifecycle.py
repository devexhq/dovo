"""Integration tests for worktree lifecycle behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitCommandError, GitPlumbingTimeoutError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeCreateStatus
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from tests.harness import WorkspaceBuilder
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
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        result = lifecycle.create(session_id="dovo_independent")

        assert result.status == WorktreeCreateStatus.OK
        assert result.session is not None
        assert not (result.session.worktree_path / ".dovo" / "run").exists()
        assert not (result.session.worktree_path / ".dovo" / "run").is_symlink()
        assert not paths.session_dir("dovo_independent").exists()

    def test_cleanup_of_worktree_containing_run_symlink_leaves_link_target_contents_intact(
        self, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        create_result = lifecycle.create(session_id="dovo_linked_cleanup")
        assert create_result.session is not None
        session = create_result.session
        external_dir = tmp_path / "external-session"
        external_dir.mkdir()
        sentinel_path = external_dir / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        link_path = session.worktree_path / ".dovo" / "run"
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(external_dir, target_is_directory=True)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not session.worktree_path.exists()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"


class WorktreeLifecycleCapacityTests:
    def test_create_at_capacity_returns_capacity_exceeded_without_creating_worktree(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        for i in range(3):
            result = lifecycle.create(session_id=f"dovo_cap_{i}")
            assert result.status == WorktreeCreateStatus.OK

        result = lifecycle.create(session_id="dovo_cap_overflow")

        assert result.status == WorktreeCreateStatus.CAPACITY_EXCEEDED
        assert not (worktree_workspace / ".dovo" / "worktrees" / "dovo_cap_overflow").exists()
        assert "dovo/dovo_cap_overflow" not in GitRunner.list_branches(worktree_workspace)


class WorktreeLifecycleCleanupTests:
    def test_cleanup_removes_dovo_directory_and_deletes_temporary_branch(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        create_result = lifecycle.create(session_id="dovo_cleanup_branch")
        assert create_result.session is not None
        session = create_result.session
        assert "dovo/dovo_cleanup_branch" in GitRunner.list_branches(worktree_workspace)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not session.worktree_path.exists()
        assert "dovo/dovo_cleanup_branch" not in GitRunner.list_branches(worktree_workspace)

    def test_cleanup_of_already_removed_worktree_is_idempotent_and_warning_free(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        create_result = lifecycle.create(session_id="dovo_cleanup_twice")
        assert create_result.session is not None
        session = create_result.session

        first_warnings = lifecycle.cleanup(session)
        second_warnings = lifecycle.cleanup(session)

        assert first_warnings == []
        assert second_warnings == []


class WorktreeLifecycleDiscardPartialTests:
    def test_discard_partial_removes_dovo_directory_and_branch(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        worktree_path = worktree_workspace / ".dovo" / "worktrees" / "dovo_discard"
        temp_branch = "dovo/dovo_discard"
        GitRunner.worktree_add(worktree_workspace, worktree_path, temp_branch, "HEAD")
        assert worktree_path.is_dir()

        lifecycle.discard_partial(worktree_path, temp_branch, created_dir=True, created_branch=True)

        assert not worktree_path.exists()
        assert temp_branch not in GitRunner.list_branches(worktree_workspace)

    def test_discard_partial_on_nonexistent_branch_does_not_raise(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        worktree_path = paths.worktrees_dir / "dovo_flagged"
        worktree_path.mkdir(parents=True)
        sentinel = worktree_path / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        temp_branch = "dovo/dovo_flagged"
        GitRunner.run(["branch", temp_branch], path=worktree_workspace)

        lifecycle.discard_partial(worktree_path, temp_branch, created_dir=created_dir, created_branch=created_branch)

        assert sentinel.exists() is not created_dir
        assert GitRunner.branch_exists(worktree_workspace, temp_branch) is not created_branch


class WorktreeLifecycleCollisionTests:
    def test_create_with_existing_worktree_id_leaves_files_branch_tip_and_worktree_list_unchanged(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo)
        first = lifecycle.create(session_id="dovo_keep")
        assert first.session is not None
        kept_file = first.session.worktree_path / "kept.txt"
        kept_file.write_text("work in progress\n", encoding="utf-8")
        tip_before = GitRunner.rev_parse(worktree_workspace, rev="dovo/dovo_keep")
        list_before = GitRunner.run(["worktree", "list", "--porcelain"], path=worktree_workspace)

        result = lifecycle.create(session_id="dovo_keep")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.errors == ["Worktree 'dovo_keep' already exists (WORKTREE_PATH_CONFLICT)."]
        assert result.fixes == [
            "Inspect it with `dovo worktree show dovo_keep` or remove it with `dovo worktree delete dovo_keep`"
        ]
        assert kept_file.read_text(encoding="utf-8") == "work in progress\n"
        assert GitRunner.rev_parse(worktree_workspace, rev="dovo/dovo_keep") == tip_before
        assert GitRunner.run(["worktree", "list", "--porcelain"], path=worktree_workspace) == list_before
        assert [record.id for record in repo.list()] == ["dovo_keep"]

    def test_create_with_only_directory_returns_already_exists_and_keeps_it(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo)
        worktree_path = paths.worktrees_dir / "dovo_half"
        worktree_path.mkdir(parents=True)
        sentinel = worktree_path / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")

        result = lifecycle.create(session_id="dovo_half")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.fixes == [f"'{worktree_path}' is not tracked by Dovo; move or remove it, then retry"]
        assert sentinel.read_text(encoding="utf-8") == "keep\n"
        assert GitRunner.branch_exists(worktree_workspace, "dovo/dovo_half") is False
        assert repo.list() == []

    def test_create_over_cleaned_row_with_leftover_directory_advises_manual_removal(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo)
        worktree_path = paths.worktrees_dir / "dovo_stale"
        worktree_path.mkdir(parents=True)
        repo.create(
            id="dovo_stale",
            name=None,
            branch_name="dovo/dovo_stale",
            base_commit=GitRunner.rev_parse(worktree_workspace, rev="HEAD"),
            worktree_path=worktree_path,
        )
        repo.update_status("dovo_stale", WorktreeStatus.CLEANED)

        result = lifecycle.create(session_id="dovo_stale")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.fixes == [f"'{worktree_path}' is not tracked by Dovo; move or remove it, then retry"]
        assert worktree_path.is_dir()

    def test_create_with_only_branch_returns_already_exists_and_keeps_it(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo)
        GitRunner.run(["branch", "dovo/dovo_half"], path=worktree_workspace)
        tip_before = GitRunner.rev_parse(worktree_workspace, rev="HEAD")

        result = lifecycle.create(session_id="dovo_half")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.fixes == [
            "Branch 'dovo/dovo_half' is not tracked by Dovo; "
            "delete it with `git branch -D dovo/dovo_half` if you no longer need it, then retry"
        ]
        assert GitRunner.rev_parse(worktree_workspace, rev="dovo/dovo_half") == tip_before
        assert (paths.worktrees_dir / "dovo_half").exists() is False
        assert repo.list() == []

    def test_create_with_dangling_symlink_at_id_returns_already_exists_and_leaves_link_target_absent(
        self, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        paths.worktrees_dir.mkdir(parents=True, exist_ok=True)
        link_target = tmp_path / "outside-worktrees"
        link_path = paths.worktrees_dir / "dovo_link"
        link_path.symlink_to(link_target, target_is_directory=True)

        result = lifecycle.create(session_id="dovo_link")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert link_path.is_symlink() is True
        assert link_target.exists() is False
        assert GitRunner.branch_exists(worktree_workspace, "dovo/dovo_link") is False

    @pytest.mark.parametrize(
        ("probe_error", "expected_status"),
        [
            pytest.param(
                GitPlumbingTimeoutError("show-ref timed out"), WorktreeCreateStatus.GIT_TIMEOUT, id="probe_timeout"
            ),
            pytest.param(
                GitCommandError(cmd=["git", "show-ref"], returncode=128, stdout="", stderr="fatal"),
                WorktreeCreateStatus.GIT_FAILED,
                id="probe_fatal_exit_128",
            ),
        ],
    )
    def test_create_when_branch_probe_fails_returns_error_status_and_creates_nothing(
        self,
        worktree_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        probe_error: Exception,
        expected_status: WorktreeCreateStatus,
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        def failing_branch_exists(path: Path, branch: str) -> bool:
            raise probe_error

        monkeypatch.setattr(GitRunner, "branch_exists", staticmethod(failing_branch_exists))

        result = lifecycle.create(session_id="dovo_probefail")

        assert result.status == expected_status
        assert (paths.worktrees_dir / "dovo_probefail").exists() is False
        assert "dovo/dovo_probefail" not in GitRunner.list_branches(worktree_workspace)

    def test_create_when_path_check_raises_oserror_returns_git_failed_path_check_message(
        self, worktree_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        real_exists = Path.exists

        def denied_exists(self: Path, *, follow_symlinks: bool = True) -> bool:
            if self.name == "dovo_denied":
                raise PermissionError("permission denied")
            return real_exists(self, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "exists", denied_exists)

        result = lifecycle.create(session_id="dovo_denied")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert result.errors == ["Worktree path check failed (WORKTREE_GIT_FAILED): permission denied"]
        assert result.fixes == [f"Check that '{paths.worktrees_dir}' is readable, then retry"]
        assert "dovo/dovo_denied" not in GitRunner.list_branches(worktree_workspace)

    def test_create_when_add_fails_and_branch_probe_raises_keeps_branch(
        self, worktree_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        def failing_branch_exists(path: Path, branch: str) -> bool:
            raise GitCommandError(cmd=["git", "show-ref"], returncode=128, stdout="", stderr="fatal")

        def add_creates_branch_then_fails(path: Path, worktree_path: Path, branch: str, base_ref: str) -> None:
            GitRunner.run(["branch", branch, base_ref], path=path)
            monkeypatch.setattr(GitRunner, "branch_exists", staticmethod(failing_branch_exists))
            raise GitCommandError(cmd=["git", "worktree", "add"], returncode=128, stdout="", stderr="boom")

        monkeypatch.setattr(GitRunner, "worktree_add", staticmethod(add_creates_branch_then_fails))

        result = lifecycle.create(session_id="dovo_keepbranch")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert "dovo/dovo_keepbranch" in GitRunner.list_branches(worktree_workspace)

    def test_create_with_bad_base_ref_on_fresh_id_returns_git_failed_and_leaves_nothing(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )
        sibling = lifecycle.create(session_id="dovo_sibling")
        assert sibling.session is not None

        result = lifecycle.create(session_id="dovo_fresh", base_ref="does-not-exist")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert (paths.worktrees_dir / "dovo_fresh").exists() is False
        assert GitRunner.branch_exists(worktree_workspace, "dovo/dovo_fresh") is False
        assert sibling.session.worktree_path.is_dir()
        assert GitRunner.branch_exists(worktree_workspace, "dovo/dovo_sibling") is True

    def test_create_when_rev_parse_fails_after_add_removes_created_worktree_and_branch(
        self, worktree_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        )

        def failing_rev_parse(path: Path, rev: str = "HEAD") -> str:
            raise GitCommandError(cmd=["git", "rev-parse", rev], returncode=128, stdout="", stderr="boom")

        monkeypatch.setattr(GitRunner, "rev_parse", staticmethod(failing_rev_parse))

        result = lifecycle.create(session_id="dovo_revfail")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert (paths.worktrees_dir / "dovo_revfail").exists() is False
        assert GitRunner.branch_exists(worktree_workspace, "dovo/dovo_revfail") is False
