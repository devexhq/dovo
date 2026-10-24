"""Integration tests for worktree lifecycle behavior."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.exceptions import GitCommandError, GitPlumbingTimeoutError
from dovo.core.git.models import GitProcessOutput
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.models import WorktreeCreateStatus
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
from tests.harness.fakes import FakeGitRunner
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
        )
        for i in range(3):
            result = lifecycle.create(session_id=f"dovo_cap_{i}")
            assert result.status == WorktreeCreateStatus.OK

        result = lifecycle.create(session_id="dovo_cap_overflow")

        assert result.status == WorktreeCreateStatus.CAPACITY_EXCEEDED
        assert not (worktree_workspace / ".dovo" / "worktrees" / "dovo_cap_overflow").exists()
        assert "dovo/dovo_cap_overflow" not in GitRunner().list_branches(worktree_workspace)


class WorktreeLifecycleCleanupTests:
    def test_cleanup_removes_dovo_directory_and_deletes_temporary_branch(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
        )
        create_result = lifecycle.create(session_id="dovo_cleanup_branch")
        assert create_result.session is not None
        session = create_result.session
        assert "dovo/dovo_cleanup_branch" in GitRunner().list_branches(worktree_workspace)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not session.worktree_path.exists()
        assert "dovo/dovo_cleanup_branch" not in GitRunner().list_branches(worktree_workspace)

    def test_cleanup_of_already_removed_worktree_is_idempotent_and_warning_free(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
        lifecycle = WorktreeLifecycle(paths, repo, git=GitRunner())
        first = lifecycle.create(session_id="dovo_keep")
        assert first.session is not None
        kept_file = first.session.worktree_path / "kept.txt"
        kept_file.write_text("work in progress\n", encoding="utf-8")
        tip_before = GitRunner().rev_parse(worktree_workspace, rev="dovo/dovo_keep")
        list_before = GitRunner().run(["worktree", "list", "--porcelain"], path=worktree_workspace)

        result = lifecycle.create(session_id="dovo_keep")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.errors == ["Worktree 'dovo_keep' already exists (WORKTREE_PATH_CONFLICT)."]
        assert result.fixes == [
            "Inspect it with `dovo worktree show dovo_keep` or remove it with `dovo worktree delete dovo_keep`"
        ]
        assert kept_file.read_text(encoding="utf-8") == "work in progress\n"
        assert GitRunner().rev_parse(worktree_workspace, rev="dovo/dovo_keep") == tip_before
        assert GitRunner().run(["worktree", "list", "--porcelain"], path=worktree_workspace) == list_before
        assert [record.id for record in repo.list()] == ["dovo_keep"]

    def test_create_with_only_directory_returns_already_exists_and_keeps_it(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo, git=GitRunner())
        worktree_path = paths.worktrees_dir / "dovo_half"
        worktree_path.mkdir(parents=True)
        sentinel = worktree_path / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")

        result = lifecycle.create(session_id="dovo_half")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.fixes == [f"'{worktree_path}' is not tracked by Dovo; move or remove it, then retry"]
        assert sentinel.read_text(encoding="utf-8") == "keep\n"
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_half") is False
        assert repo.list() == []

    def test_create_over_cleaned_row_with_leftover_directory_advises_manual_removal(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo, git=GitRunner())
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

        result = lifecycle.create(session_id="dovo_stale")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.fixes == [f"'{worktree_path}' is not tracked by Dovo; move or remove it, then retry"]
        assert worktree_path.is_dir()

    def test_create_with_only_branch_returns_already_exists_and_keeps_it(self, worktree_workspace: Path) -> None:
        paths = _paths(worktree_workspace)
        repo = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
        lifecycle = WorktreeLifecycle(paths, repo, git=GitRunner())
        GitRunner().run(["branch", "dovo/dovo_half"], path=worktree_workspace)
        tip_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")

        result = lifecycle.create(session_id="dovo_half")

        assert result.status == WorktreeCreateStatus.ALREADY_EXISTS
        assert result.error_code == "WORKTREE_PATH_CONFLICT"
        assert result.fixes == [
            "Branch 'dovo/dovo_half' is not tracked by Dovo; "
            "delete it with `git branch -D dovo/dovo_half` if you no longer need it, then retry"
        ]
        assert GitRunner().rev_parse(worktree_workspace, rev="dovo/dovo_half") == tip_before
        assert (paths.worktrees_dir / "dovo_half").exists() is False
        assert repo.list() == []

    def test_create_with_dangling_symlink_at_id_returns_already_exists_and_leaves_link_target_absent(
        self, worktree_workspace: Path, tmp_path: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_link") is False

    @pytest.mark.parametrize(
        ("probe_error", "expected_status", "expected_error_prefix"),
        [
            pytest.param(
                GitPlumbingTimeoutError("show-ref timed out"),
                WorktreeCreateStatus.GIT_TIMEOUT,
                "Git worktree operation timed out (WORKTREE_GIT_TIMEOUT): show-ref timed out",
                id="probe_timeout",
            ),
            pytest.param(
                GitCommandError(cmd=["git", "show-ref"], returncode=128, stdout="", stderr="fatal"),
                WorktreeCreateStatus.GIT_FAILED,
                "Git worktree operation failed (WORKTREE_GIT_FAILED): ",
                id="probe_fatal_exit_128",
            ),
        ],
    )
    def test_create_when_branch_probe_fails_returns_error_status_and_creates_nothing(
        self,
        worktree_workspace: Path,
        probe_error: Exception,
        expected_status: WorktreeCreateStatus,
        expected_error_prefix: str,
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths,
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=FakeGitRunner({("show-ref",): probe_error}),
        )

        result = lifecycle.create(session_id="dovo_probefail")

        assert result.status == expected_status
        assert len(result.errors) == 1
        assert result.errors[0].startswith(expected_error_prefix)
        assert (paths.worktrees_dir / "dovo_probefail").exists() is False
        assert "dovo/dovo_probefail" not in GitRunner().list_branches(worktree_workspace)

    def test_create_when_path_check_raises_oserror_returns_git_failed_path_check_message(
        self, worktree_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
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
                    raise GitCommandError(cmd=["git", "worktree", "add"], returncode=128, stdout="", stderr="boom")
                if self.added and args[0] == "show-ref":
                    raise GitCommandError(cmd=["git", "show-ref"], returncode=128, stdout="", stderr="fatal")
                return super().exec(args, path, input_text=input_text, env=env)

        lifecycle = WorktreeLifecycle(
            paths,
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=_AddCreatesBranchThenFails(),
        )

        result = lifecycle.create(session_id="dovo_keepbranch")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert "dovo/dovo_keepbranch" in GitRunner().list_branches(worktree_workspace)

    def test_create_with_bad_base_ref_on_fresh_id_returns_git_failed_and_leaves_nothing(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        lifecycle = WorktreeLifecycle(
            paths, WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id), git=GitRunner()
        )
        sibling = lifecycle.create(session_id="dovo_sibling")
        assert sibling.session is not None

        result = lifecycle.create(session_id="dovo_fresh", base_ref="does-not-exist")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert (paths.worktrees_dir / "dovo_fresh").exists() is False
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_fresh") is False
        assert sibling.session.worktree_path.is_dir()
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_sibling") is True

    def test_create_when_rev_parse_fails_after_add_removes_created_worktree_and_branch(
        self, worktree_workspace: Path
    ) -> None:
        paths = _paths(worktree_workspace)
        failure = GitCommandError(cmd=["git", "rev-parse", "HEAD"], returncode=128, stdout="", stderr="boom")
        lifecycle = WorktreeLifecycle(
            paths,
            WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
            git=FakeGitRunner({("rev-parse", "HEAD"): failure}),
        )

        result = lifecycle.create(session_id="dovo_revfail")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert (paths.worktrees_dir / "dovo_revfail").exists() is False
        assert GitRunner().branch_exists(worktree_workspace, "dovo/dovo_revfail") is False


def _lifecycle(workspace: Path, git: GitRunner | None = None) -> WorktreeLifecycle:
    """Build a WorktreeLifecycle over workspace's paths and worktree database, running git through git or a real GitRunner."""
    paths = _paths(workspace)
    return WorktreeLifecycle(
        paths,
        WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id),
        git=git if git is not None else GitRunner(),
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


class WorktreeLifecycleDiagnosticLogTests:
    @pytest.mark.parametrize(
        ("target", "prefix"),
        [
            pytest.param(
                "branch_exists", "Git worktree operation failed (WORKTREE_GIT_FAILED): boom", id="branch_exists"
            ),
            pytest.param(
                "worktree_add", "Git worktree operation failed (WORKTREE_GIT_FAILED): boom", id="worktree_add"
            ),
            pytest.param("rev_parse", "Git worktree operation failed (WORKTREE_GIT_FAILED): boom", id="rev_parse"),
        ],
    )
    def test_create_unexpected_failure_records_error_and_appends_hint(
        self,
        worktree_workspace: Path,
        frozen_diagnostic_clock: Path,
        target: str,
        prefix: str,
    ) -> None:
        """Return git_failed and errors == [f"{prefix}\nDetails logged: {path} (event exc_3f9a1c07b2de)"], with one error record for worktree.create whose context is {worktree_id, branch, path}."""
        lifecycle = _lifecycle(worktree_workspace, FakeGitRunner({_GIT_KEYS[target]: RuntimeError("boom")}))
        worktree_path = lifecycle.worktree_base_dir / "dovo_boom"
        if target != "branch_exists":
            worktree_path = worktree_path.resolve()

        result = lifecycle.create(session_id="dovo_boom")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert result.errors == [f"{prefix}{diagnostic_hint(frozen_diagnostic_clock)}"]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["context"] == {"worktree_id": "dovo_boom", "branch": "dovo/dovo_boom", "path": str(worktree_path)}
        assert event["exception_type"] == "RuntimeError"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_wip_copy_failure_records_error_and_appends_hint(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path
    ) -> None:
        """Return wip_failed with errors[0] starting "Failed to overlay uncommitted WIP into worktree (WORKTREE_WIP_FAILED): " and ending with the hint, and one error record for worktree.create with exception_type PermissionError, when create(include_wip=True) copies an untracked WIP file chmod 000."""
        lifecycle = _lifecycle(worktree_workspace)
        unreadable = worktree_workspace / "secret.txt"
        unreadable.write_text("wip\n", encoding="utf-8")
        unreadable.chmod(0o000)
        try:
            result = lifecycle.create(session_id="dovo_wip", include_wip=True)
        finally:
            unreadable.chmod(0o644)

        assert result.status == WorktreeCreateStatus.WIP_FAILED
        assert result.errors[0].startswith("Failed to overlay uncommitted WIP into worktree (WORKTREE_WIP_FAILED): ")
        assert result.errors[0].endswith(diagnostic_hint(frozen_diagnostic_clock))
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.create"
        assert event["exception_type"] == "PermissionError"

    def test_wip_domain_error_writes_no_record(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return wip_failed with the unhinted error and leave <DOVO_HOME>/logs absent when apply_wip_to_worktree raises WorktreeError("worktree path does not exist: x") with no cause."""
        lifecycle = _lifecycle(worktree_workspace)

        def missing_worktree(*, git: GitRunner, source_root: Path, worktree_path: Path) -> list[str]:
            raise WorktreeError("worktree path does not exist: x")

        monkeypatch.setattr("dovo.core.worktree.services.lifecycle.apply_wip_to_worktree", missing_worktree)

        result = lifecycle.create(session_id="dovo_wipdomain", include_wip=True)

        assert result.status == WorktreeCreateStatus.WIP_FAILED
        assert result.errors == [
            "Failed to overlay uncommitted WIP into worktree (WORKTREE_WIP_FAILED): worktree path does not exist: x"
        ]
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_persist_failure_records_error_and_hints_warning(
        self, worktree_workspace: Path, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return status ok with warnings == ["Failed to persist worktree metadata to the local database: boom\nDetails logged: ..."] when WorktreesRepository.create raises RuntimeError("boom")."""
        lifecycle = _lifecycle(worktree_workspace)
        monkeypatch.setattr(WorktreesRepository, "create", _raise_boom)

        result = lifecycle.create(session_id="dovo_persist")

        assert result.status == WorktreeCreateStatus.OK
        assert result.warnings == [
            f"Failed to persist worktree metadata to the local database: boom{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
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
        """Return git_failed with an unhinted error when worktree_add raises GitCommandError and the follow-up branch probe raises RuntimeError, and write one debug record for worktree.create."""

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

        failure = GitCommandError(cmd=["git", "worktree", "add"], returncode=128, stdout="", stderr="fatal")
        lifecycle = _lifecycle(worktree_workspace, _SecondProbeFails({("worktree", "add"): failure}))

        result = lifecycle.create(session_id="dovo_probe")

        assert result.status == WorktreeCreateStatus.GIT_FAILED
        assert len(result.errors) == 1
        assert result.errors[0].startswith("Git worktree operation failed (WORKTREE_GIT_FAILED): ")
        assert "Details logged:" not in result.errors[0]
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
    def test_cleanup_unexpected_failure_records_error_and_hints_warning(
        self,
        worktree_workspace: Path,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
        prefix: str,
    ) -> None:
        """Return warnings == [f"{prefix}\nDetails logged: ..."] from cleanup() and write one error record for worktree.cleanup with context {worktree_id, branch, path}."""
        fake = FakeGitRunner()
        lifecycle = _lifecycle(worktree_workspace, fake)
        session = lifecycle.create(session_id="dovo_clean").session
        assert session is not None
        if target == "update_status":
            monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        else:
            fake.responses[_GIT_KEYS[target]] = RuntimeError("boom")

        warnings = lifecycle.cleanup(session)

        assert warnings == [f"{prefix}{diagnostic_hint(frozen_diagnostic_clock)}"]
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
        """With git worktree remove raising RuntimeError, return no directory warning and one debug record when rmtree succeeds, or a hinted directory warning and one error record when worktrees_dir is read-only."""
        fake = FakeGitRunner()
        lifecycle = _lifecycle(worktree_workspace, fake)
        session = lifecycle.create(session_id="dovo_fallback").session
        assert session is not None
        fake.responses[("worktree", "remove")] = RuntimeError("boom")
        if rmtree_fails:
            lifecycle.worktree_base_dir.chmod(0o555)
        try:
            warnings = lifecycle.cleanup(session)
        finally:
            lifecycle.worktree_base_dir.chmod(0o755)

        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "worktree.cleanup"
        assert event["context"] == {"path": str(session.worktree_path)}
        dir_warnings = [warning for warning in warnings if warning.startswith("Failed to remove worktree directory")]
        if rmtree_fails:
            assert len(dir_warnings) == 1
            assert dir_warnings[0].startswith(
                f"Failed to remove worktree directory at '{session.worktree_path}': boom; fallback removal failed: "
            )
            assert dir_warnings[0].endswith(diagnostic_hint(frozen_diagnostic_clock))
            assert event["level"] == "error"
            assert event["exception_type"] == "PermissionError"
        else:
            assert dir_warnings == []
            assert not session.worktree_path.exists()
            assert event["level"] == "debug"
            assert event["exception_type"] == "RuntimeError"

    def test_repeated_cleanup_writes_no_record(self, worktree_workspace: Path, frozen_diagnostic_clock: Path) -> None:
        """Return [] from a second cleanup() of the same worktree and leave <DOVO_HOME>/logs absent."""
        lifecycle = _lifecycle(worktree_workspace)
        session = lifecycle.create(session_id="dovo_twice").session
        assert session is not None
        lifecycle.cleanup(session)

        warnings = lifecycle.cleanup(session)

        assert warnings == []
        assert not resolve_global_paths(None).logs_dir.exists()
