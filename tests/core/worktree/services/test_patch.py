"""Contract tests for worktree conflict extraction, diff inspection, and patch application."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.models import Remediation
from dovo.core.db import WorktreesRepository
from dovo.core.db.models import WorktreeStatus
from dovo.core.git.exceptions import GitError, git_command_error
from dovo.core.git.models import GitProcessOutput
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeApplyStrategy
from dovo.core.worktree.services.patch import extract_conflicts
from dovo.core.worktree.worktree import Worktree
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id
from tests.harness.git_index import snapshot_index, temp_index_files


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
            pytest.param("error: src/gone.py: No such file or directory\n", ["src/gone.py"], id="missing_in_workspace"),
            pytest.param("Applying patch\nnothing to see here\n", [], id="no_conflict_markers"),
        ],
    )
    def test_extracts_conflicting_paths_from_known_git_apply_stderr_patterns(
        self, stderr: str, expected: list[str]
    ) -> None:
        assert extract_conflicts(stderr) == expected

    def test_deduplicates_and_sorts_multiple_conflicting_paths(self) -> None:
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


def _worktree(paths: WorkspacePaths, db: WorktreesRepository, *, git: GitRunner) -> Worktree:
    """Build a Worktree over db that runs git through git."""
    return Worktree(paths, db=db, git=git, clock=fixed_clock, new_id=fixed_new_id)


def _create_worktree_with_change(paths: WorkspacePaths, worktree_id: str) -> None:
    """Create a real worktree and commit one file change inside it."""
    result = Worktree(paths, db=_repo(paths), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id).create(
        session_id=worktree_id
    )
    assert result.session is not None
    worktree_path = result.session.worktree_path
    (worktree_path / "new_file.txt").write_text("worktree change\n", encoding="utf-8")
    GitRunner().run(["add", "-A"], path=worktree_path)
    GitRunner().run(["commit", "-m", "worktree change"], path=worktree_path)


class WorktreePatchDiffTests:
    def test_unknown_worktree_id_reports_not_found_with_list_remediation(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return error_code WORKTREE_NOT_FOUND, errors == ["Worktree 'missing_worktree' not found."] and the 'dovo worktree list' remediation."""
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.diff("missing_worktree")

        assert result.error_code == ErrorCode.WORKTREE_NOT_FOUND
        assert result.errors == ["Worktree 'missing_worktree' not found."]
        assert result.remediations == [
            Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")
        ]

    def test_worktree_directory_missing_on_disk_reports_worktree_missing(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return error_code WORKTREE_MISSING, errors == ["Worktree 'dovo_gone' directory is missing on disk."], and leave the row cleaned."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_gone")
        record = db.get("dovo_gone")
        assert record is not None
        shutil.rmtree(record.worktree_path)
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.diff("dovo_gone")

        assert result.error_code == ErrorCode.WORKTREE_MISSING
        assert result.errors == ["Worktree 'dovo_gone' directory is missing on disk."]
        reconciled = db.get("dovo_gone")
        assert reconciled is not None
        assert reconciled.status == WorktreeStatus.CLEANED

    def test_worktree_with_no_changes_is_an_empty_success(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return error_code None, errors == [], warnings == [], files_changed == [] and diff_text == "" for a fresh worktree."""
        db = _repo(worktree_workspace_paths)
        create_result = Worktree(
            worktree_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(session_id="dovo_empty")
        assert create_result.session is not None
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.diff("dovo_empty")

        assert result.error_code is None
        assert result.errors == []
        assert result.warnings == []
        assert result.files_changed == []
        assert result.diff_text == ""

    def test_worktree_with_committed_change_returns_ok_with_touched_files(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_diffme")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.diff("dovo_diffme")

        assert result.ok
        assert "new_file.txt" in result.files_changed
        assert result.diff_text.strip() != ""

    @pytest.mark.parametrize(
        ("stat", "expect_stat_text"),
        [
            pytest.param(False, False, id="default_omits_diffstat"),
            pytest.param(True, True, id="stat_includes_diffstat"),
        ],
    )
    def test_stat_flag_controls_whether_diffstat_is_returned(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
        stat: bool,
        expect_stat_text: bool,
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_stat")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.diff("dovo_stat", stat=stat)

        assert result.ok
        assert "diff --git" in result.diff_text
        assert bool(result.stat_text) is expect_stat_text
        if expect_stat_text:
            assert "new_file.txt" in result.stat_text


class WorktreePatchApplyTests:
    def test_unknown_worktree_id_reports_not_found_with_list_remediation(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return WORKTREE_NOT_FOUND, errors == ["Worktree 'missing_worktree' not found."] and the 'dovo worktree list' remediation."""
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("missing_worktree")

        assert result.error_code == ErrorCode.WORKTREE_NOT_FOUND
        assert result.errors == ["Worktree 'missing_worktree' not found."]
        assert result.remediations == [
            Remediation(message="Run 'dovo worktree list' to see known worktrees", command="dovo worktree list")
        ]

    @pytest.mark.parametrize("directory", ["present", "removed_by_apply_delete"])
    def test_already_merged_worktree_reports_already_merged_without_reapplying(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, directory: str
    ) -> None:
        """Return WORKTREE_ALREADY_MERGED, errors == ["Worktree 'dovo_merged' is already merged."], warnings == [], leave the main repository unchanged and the row merged.

        removed_by_apply_delete: the first apply runs with delete=True, so the second apply finds a merged row with no directory.
        """
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_merged")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())
        if directory == "present":
            db.update_status("dovo_merged", WorktreeStatus.MERGED)
        else:
            assert worktree.apply("dovo_merged", delete=True).ok
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        status_before = GitRunner().status_porcelain(worktree_workspace)

        result = worktree.apply("dovo_merged")

        assert result.error_code == ErrorCode.WORKTREE_ALREADY_MERGED
        assert result.errors == ["Worktree 'dovo_merged' is already merged."]
        assert result.warnings == []
        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before
        assert GitRunner().status_porcelain(worktree_workspace) == status_before
        record = db.get("dovo_merged")
        assert record is not None
        assert record.status == WorktreeStatus.MERGED

    @pytest.mark.parametrize(
        "strategy",
        [
            pytest.param(WorktreeApplyStrategy.PATCH, id="patch"),
            pytest.param(WorktreeApplyStrategy.SQUASH, id="squash"),
        ],
    )
    def test_dirty_main_repo_without_allow_dirty_reports_git_dirty_worktree(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, strategy: WorktreeApplyStrategy
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_dirty_main")
        (worktree_workspace / "uncommitted.txt").write_text("wip", encoding="utf-8")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.apply("dovo_dirty_main", strategy=strategy)

        assert result.error_code == ErrorCode.GIT_DIRTY_WORKTREE
        assert result.strategy == strategy
        assert result.errors == ["Cannot apply worktree dovo_dirty_main: main repository has uncommitted changes."]
        assert result.remediations == [
            Remediation(message="Commit or stash local changes in the main workspace"),
            Remediation(message="Pass '--allow-dirty' to overlay changes anyway"),
        ]

    def test_worktree_with_no_changes_reports_patch_empty(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return PATCH_EMPTY, errors == ["Worktree 'dovo_empty' has no changes to apply."] and warnings == []."""
        db = _repo(worktree_workspace_paths)
        create_result = Worktree(
            worktree_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(session_id="dovo_empty")
        assert create_result.session is not None
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.apply("dovo_empty")

        assert result.error_code == ErrorCode.PATCH_EMPTY
        assert result.errors == ["Worktree 'dovo_empty' has no changes to apply."]
        assert result.warnings == []

    def test_dry_run_reports_ok_without_modifying_main_workspace(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_dry_run")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.apply("dovo_dry_run", dry_run=True)

        assert result.ok
        assert any("dry run" in w.lower() for w in result.warnings)
        assert not (worktree_workspace / "new_file.txt").exists()

    def test_patch_strategy_applies_changes_and_marks_worktree_merged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_apply_patch")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.apply("dovo_apply_patch", strategy=WorktreeApplyStrategy.PATCH)

        assert result.ok
        assert (worktree_workspace / "new_file.txt").read_text(encoding="utf-8") == "worktree change\n"
        record = db.get("dovo_apply_patch")
        assert record is not None
        assert record.status == WorktreeStatus.MERGED

    def test_squash_strategy_creates_single_commit_with_default_message(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_squash")
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.apply("dovo_squash", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.ok
        assert result.commit_sha is not None
        log_message = GitRunner().run(["log", "-1", "--format=%s"], path=worktree_workspace).strip()
        assert log_message == "dovo: apply changes from worktree dovo_squash"

    @pytest.mark.parametrize(
        ("strategy", "dry_run"),
        [
            pytest.param(WorktreeApplyStrategy.SQUASH, False, id="squash"),
            pytest.param(WorktreeApplyStrategy.PATCH, True, id="patch_dry_run"),
        ],
    )
    def test_squash_and_patch_dry_run_conflicts_report_conflicting_files(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
        strategy: WorktreeApplyStrategy,
        dry_run: bool,
    ) -> None:
        """Return WORKTREE_APPLY_CONFLICT with the strategy, conflicting_files == ["new_file.txt"], the full conflict message and touched_files == []."""
        _create_conflicting_worktree(worktree_workspace_paths, worktree_workspace, "dovo_conflict")
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_conflict", strategy=strategy, dry_run=dry_run)

        assert result.error_code == ErrorCode.WORKTREE_APPLY_CONFLICT
        assert result.worktree_id == "dovo_conflict"
        assert result.strategy == strategy
        assert result.conflicting_files == ["new_file.txt"]
        assert result.touched_files == []
        assert result.errors == [
            "Cannot apply worktree dovo_conflict: conflicts detected.\nConflicting files:\n  • new_file.txt"
        ]


_SEED_FILES = {
    "notes.txt": "notes\n",
    "staged.txt": "staged\n",
    "src/app.py": "app\n",
    "old_name.txt": "rename me\n",
    "gone.txt": "delete me\n",
    "é_tracked.txt": "tracked\n",
}


def _seed_main_repo(workspace: Path) -> None:
    """Commit the files every squash scope test's worktree base must contain."""
    for relative_path, content in _SEED_FILES.items():
        (workspace / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / relative_path).write_text(content, encoding="utf-8")
    GitRunner().run(["add", "-A"], path=workspace)
    GitRunner().run(["commit", "-m", "Seed squash scope files"], path=workspace)


def _create_worktree_with_changes(
    paths: WorkspacePaths,
    worktree_id: str,
    *,
    write: dict[str, str],
    delete: list[str] | None = None,
    rename: dict[str, str] | None = None,
) -> None:
    """Create a real worktree and commit the given writes, deletions and renames inside it."""
    result = Worktree(paths, db=_repo(paths), git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id).create(
        session_id=worktree_id
    )
    assert result.session is not None
    worktree_path = result.session.worktree_path
    for relative_path, content in write.items():
        (worktree_path / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (worktree_path / relative_path).write_text(content, encoding="utf-8")
    for relative_path in delete or []:
        GitRunner().run(["rm", "-q", relative_path], path=worktree_path)
    for source, destination in (rename or {}).items():
        GitRunner().run(["mv", source, destination], path=worktree_path)
    GitRunner().run(["add", "-A"], path=worktree_path)
    GitRunner().run(["commit", "-m", "worktree changes"], path=worktree_path)


def _make_unrelated_paths_dirty(workspace: Path) -> None:
    """Leave notes.txt unstaged, staged.txt staged and scratch.txt untracked in the main repository."""
    (workspace / "notes.txt").write_text("notes edited\n", encoding="utf-8")
    (workspace / "staged.txt").write_text("staged edited\n", encoding="utf-8")
    GitRunner().run(["add", "staged.txt"], path=workspace)
    (workspace / "scratch.txt").write_text("scratch\n", encoding="utf-8")


def _committed_paths(workspace: Path) -> list[str]:
    """List the paths HEAD's commit records, both sides of a rename included."""
    output = GitRunner().run(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", "HEAD"], path=workspace
    )
    return output.splitlines()


def _overlap_ascii_mixed(workspace: Path) -> None:
    (workspace / "gone.txt").write_text("user edit\n", encoding="utf-8")
    (workspace / "src/app.py").write_text("user edit\n", encoding="utf-8")
    GitRunner().run(["add", "src/app.py"], path=workspace)
    (workspace / "src/new.py").write_text("user file\n", encoding="utf-8")


def _overlap_non_ascii_staged(workspace: Path) -> None:
    (workspace / "é_tracked.txt").write_text("user edit\n", encoding="utf-8")
    GitRunner().run(["add", "é_tracked.txt"], path=workspace)


def _overlap_non_ascii_unstaged(workspace: Path) -> None:
    (workspace / "é_tracked.txt").write_text("user edit\n", encoding="utf-8")


def _overlap_non_ascii_untracked(workspace: Path) -> None:
    (workspace / "é_new.txt").write_text("user file\n", encoding="utf-8")


_OVERLAP_CASES: dict[str, tuple[dict[str, str], list[str], Callable[[Path], None], str]] = {
    "ascii_mixed": (
        {"src/app.py": "worktree edit\n", "src/new.py": "worktree file\n"},
        ["gone.txt"],
        _overlap_ascii_mixed,
        "  • gone.txt\n  • src/app.py\n  • src/new.py",
    ),
    "non_ascii_staged": ({"é_tracked.txt": "worktree edit\n"}, [], _overlap_non_ascii_staged, "  • é_tracked.txt"),
    "non_ascii_unstaged": ({"é_tracked.txt": "worktree edit\n"}, [], _overlap_non_ascii_unstaged, "  • é_tracked.txt"),
    "non_ascii_untracked": ({"é_new.txt": "worktree file\n"}, [], _overlap_non_ascii_untracked, "  • é_new.txt"),
}


class WorktreeSquashScopeTests:
    def test_squash_commit_contains_exactly_patch_paths_including_both_sides_of_rename(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        _seed_main_repo(worktree_workspace)
        _create_worktree_with_changes(
            worktree_workspace_paths,
            "dovo_scope",
            write={"src/app.py": "worktree edit\n", "src/new.py": "worktree file\n"},
            delete=["gone.txt"],
            rename={"old_name.txt": "new_name.txt"},
        )
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_scope", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.ok
        assert _committed_paths(worktree_workspace) == [
            "gone.txt",
            "new_name.txt",
            "old_name.txt",
            "src/app.py",
            "src/new.py",
        ]
        assert GitRunner().status_porcelain(worktree_workspace) == []

    def test_squash_with_allow_dirty_preserves_unrelated_staged_unstaged_and_untracked_paths(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        _seed_main_repo(worktree_workspace)
        _create_worktree_with_changes(worktree_workspace_paths, "dovo_unrelated", write={"src/app.py": "edit\n"})
        _make_unrelated_paths_dirty(worktree_workspace)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_unrelated", strategy=WorktreeApplyStrategy.SQUASH, allow_dirty=True)

        assert result.ok
        assert _committed_paths(worktree_workspace) == ["src/app.py"]
        assert GitRunner().status_porcelain(worktree_workspace) == [" M notes.txt", "M  staged.txt", "?? scratch.txt"]
        assert (worktree_workspace / "notes.txt").read_text(encoding="utf-8") == "notes edited\n"
        assert (worktree_workspace / "staged.txt").read_text(encoding="utf-8") == "staged edited\n"
        assert (worktree_workspace / "scratch.txt").read_text(encoding="utf-8") == "scratch\n"
        assert GitRunner().run(["show", ":notes.txt"], path=worktree_workspace) == "notes"
        assert GitRunner().run(["show", ":staged.txt"], path=worktree_workspace) == "staged edited"

    @pytest.mark.parametrize("dry_run", [pytest.param(False, id="apply"), pytest.param(True, id="dry_run")])
    @pytest.mark.parametrize(
        "case",
        [
            pytest.param("ascii_mixed", id="ascii_mixed"),
            pytest.param("non_ascii_staged", id="non_ascii_staged"),
            pytest.param("non_ascii_unstaged", id="non_ascii_unstaged"),
            pytest.param("non_ascii_untracked", id="non_ascii_untracked"),
        ],
    )
    def test_squash_with_allow_dirty_refuses_overlapping_dirty_paths_without_changing_anything(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, dry_run: bool, case: str
    ) -> None:
        write, delete, make_overlap, expected_bullets = _OVERLAP_CASES[case]
        _seed_main_repo(worktree_workspace)
        _create_worktree_with_changes(worktree_workspace_paths, "dovo_overlap", write=write, delete=delete)
        make_overlap(worktree_workspace)
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        dirty_before = GitRunner().dirty_paths(worktree_workspace)
        porcelain_before = GitRunner().status_porcelain(worktree_workspace)
        db = _repo(worktree_workspace_paths)
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        result = worktree.apply(
            "dovo_overlap", strategy=WorktreeApplyStrategy.SQUASH, allow_dirty=True, dry_run=dry_run
        )

        assert result.error_code == ErrorCode.GIT_DIRTY_WORKTREE
        assert result.strategy == WorktreeApplyStrategy.SQUASH
        assert result.errors == [
            "Cannot squash worktree dovo_overlap: the main repository has uncommitted changes in files the worktree "
            f"also changes.\nOverlapping files:\n{expected_bullets}"
        ]
        assert result.remediations == [
            Remediation(message="Commit or stash those files in the main workspace"),
            Remediation(message="Use '--strategy patch' to overlay the changes without committing"),
        ]
        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before
        assert GitRunner().dirty_paths(worktree_workspace) == dirty_before
        assert GitRunner().status_porcelain(worktree_workspace) == porcelain_before
        record = db.get("dovo_overlap")
        assert record is not None
        assert record.status == WorktreeStatus.ACTIVE

    def test_failed_squash_commit_reports_git_failed_and_creates_no_commit(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Install the hook only after the worktree's own commit: linked worktrees share the hooks directory."""
        _seed_main_repo(worktree_workspace)
        _create_worktree_with_changes(worktree_workspace_paths, "dovo_hook", write={"src/app.py": "edit\n"})
        _make_unrelated_paths_dirty(worktree_workspace)
        hooks_dir = worktree_workspace / GitRunner().run(["rev-parse", "--git-path", "hooks"], path=worktree_workspace)
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_hook", strategy=WorktreeApplyStrategy.SQUASH, allow_dirty=True)

        assert result.error_code == ErrorCode.GIT_FAILED
        assert result.errors[0].startswith("Git failed during squash commit for worktree 'dovo_hook': ")
        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before
        assert GitRunner().status_porcelain(worktree_workspace) == [
            " M notes.txt",
            "M  src/app.py",
            "M  staged.txt",
            "?? scratch.txt",
        ]
        assert GitRunner().run(["show", ":staged.txt"], path=worktree_workspace) == "staged edited"

    def test_rename_only_squash_commits_old_and_new_path(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        _seed_main_repo(worktree_workspace)
        _create_worktree_with_changes(
            worktree_workspace_paths, "dovo_rename", write={}, rename={"old_name.txt": "new_name.txt"}
        )
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_rename", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.ok
        assert _committed_paths(worktree_workspace) == ["new_name.txt", "old_name.txt"]


_NEUTRALITY_FILES = ["committed.txt", "new.txt", "notes.txt"]


def _create_worktree_with_untracked_file(paths: WorkspacePaths, worktree_id: str) -> Path:
    """Create a worktree with committed committed.txt, modified tracked notes.txt and untracked new.txt."""
    _seed_main_repo(paths.root_dir)
    _create_worktree_with_changes(paths, worktree_id, write={"committed.txt": "committed\n"})
    record = _repo(paths).get(worktree_id)
    assert record is not None
    worktree_path = Path(record.worktree_path)
    (worktree_path / "notes.txt").write_text("notes edited\n", encoding="utf-8")
    (worktree_path / "new.txt").write_text("untracked\n", encoding="utf-8")
    return worktree_path


def _remove_worktree_index(paths: WorkspacePaths, worktree_id: str) -> Path:
    """Create a worktree with one committed change, delete its index file and return the worktree path."""
    _create_worktree_with_change(paths, worktree_id)
    record = _repo(paths).get(worktree_id)
    assert record is not None
    worktree_path = Path(record.worktree_path)
    (worktree_path / GitRunner().run(["rev-parse", "--git-path", "index"], path=worktree_path)).unlink()
    return worktree_path


class WorktreePatchIndexNeutralityTests:
    @pytest.mark.parametrize("stat", [pytest.param(False, id="default"), pytest.param(True, id="stat")])
    def test_diff_leaves_worktree_index_and_status_unchanged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, stat: bool
    ) -> None:
        worktree_path = _create_worktree_with_untracked_file(worktree_workspace_paths, "dovo_neutral_diff")
        before = snapshot_index(worktree_path)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.diff("dovo_neutral_diff", stat=stat)

        assert result.ok
        assert result.files_changed == _NEUTRALITY_FILES
        assert "diff --git a/new.txt b/new.txt" in result.diff_text
        if stat:
            for name in _NEUTRALITY_FILES:
                assert name in result.stat_text
        assert snapshot_index(worktree_path) == before
        assert "?? new.txt" in before.status.splitlines()

    @pytest.mark.parametrize("dry_run", [pytest.param(True, id="dry_run"), pytest.param(False, id="apply")])
    def test_apply_leaves_worktree_index_and_status_unchanged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, dry_run: bool
    ) -> None:
        worktree_path = _create_worktree_with_untracked_file(worktree_workspace_paths, "dovo_neutral_apply")
        before = snapshot_index(worktree_path)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_neutral_apply", dry_run=dry_run)

        assert result.ok
        assert result.touched_files == _NEUTRALITY_FILES
        assert snapshot_index(worktree_path) == before


class WorktreePatchDiffFailureTests:
    def test_missing_worktree_index_reports_internal_error_and_leaves_no_temp_index(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return INTERNAL_ERROR with errors[0] starting "Unexpected error during worktree.diff: ", one diagnostics entry, and no temp index file."""
        worktree_path = _remove_worktree_index(worktree_workspace_paths, "dovo_noindex")
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.diff("dovo_noindex")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors[0].startswith("Unexpected error during worktree.diff: ")
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert temp_index_files(worktree_path) == []

    def test_apply_with_missing_worktree_index_reports_internal_error_and_leaves_main_repo_unchanged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return INTERNAL_ERROR with errors[0] starting "Unexpected error during worktree.apply: ", one diagnostics entry, HEAD and git status unchanged, and no temp index file."""
        worktree_path = _remove_worktree_index(worktree_workspace_paths, "dovo_noindex_apply")
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        status_before = GitRunner().status_porcelain(worktree_workspace)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_noindex_apply")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors[0].startswith("Unexpected error during worktree.apply: ")
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before
        assert GitRunner().status_porcelain(worktree_workspace) == status_before
        assert temp_index_files(worktree_path) == []


class WorktreePatchGitFailureTests:
    def test_diff_git_timeout_reports_git_timeout(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """Return GIT_TIMEOUT and errors == ["Git timed out while generating the diff for worktree 'dovo_t': <detail>"] when ("diff", "--cached") raises a GIT_TIMEOUT GitError, writing no event file."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        detail = "Git timed out after 30s ('git diff --cached')"
        fake = FakeGitRunner({("diff", "--cached"): GitError(ErrorCode.GIT_TIMEOUT, detail)})
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.diff("dovo_t")

        assert result.error_code == ErrorCode.GIT_TIMEOUT
        assert result.errors == [f"Git timed out while generating the diff for worktree 'dovo_t': {detail}"]
        assert result.diagnostics == []
        assert not resolve_global_paths(None).logs_dir.exists()

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            pytest.param(
                git_command_error(["diff"], 128, "", "fatal: bad revision"),
                ErrorCode.GIT_FAILED,
                id="git_failed",
            ),
            pytest.param(
                GitError(ErrorCode.GIT_BINARY_MISSING, "Git execution failed ('git diff'): git not found"),
                ErrorCode.GIT_BINARY_MISSING,
                id="git_binary_missing",
            ),
        ],
    )
    def test_diff_git_failure_reports_its_code(
        self, worktree_workspace_paths: WorkspacePaths, error: GitError, code: ErrorCode
    ) -> None:
        """Return the git error's code and "Git failed while generating the diff for worktree '<id>': <exc>", writing no event file."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        fake = FakeGitRunner({("diff", "--cached"): error})
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.diff("dovo_t")

        assert result.error_code == code
        assert result.errors == [f"Git failed while generating the diff for worktree 'dovo_t': {error}"]
        assert result.diagnostics == []
        assert not resolve_global_paths(None).logs_dir.exists()

    @pytest.mark.parametrize(
        ("git_key", "strategy", "allow_dirty", "phase"),
        [
            pytest.param(
                ("diff", "--cached"), WorktreeApplyStrategy.PATCH, False, "diff generation", id="diff_generation"
            ),
            pytest.param(
                ("status", "--porcelain", "-u"), WorktreeApplyStrategy.PATCH, False, "conflict check", id="status_check"
            ),
            pytest.param(
                ("status", "--porcelain=v1"), WorktreeApplyStrategy.SQUASH, True, "conflict check", id="overlap_check"
            ),
            pytest.param(
                ("apply", "--check"), WorktreeApplyStrategy.PATCH, False, "conflict check", id="conflict_check"
            ),
            pytest.param(("apply", "--binary"), WorktreeApplyStrategy.PATCH, False, "patch apply", id="patch_apply"),
            pytest.param(
                ("--literal-pathspecs", "commit"),
                WorktreeApplyStrategy.SQUASH,
                False,
                "squash commit",
                id="squash_commit",
            ),
        ],
    )
    def test_apply_git_timeout_reports_git_timeout_with_phase(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
        git_key: tuple[str, ...],
        strategy: WorktreeApplyStrategy,
        allow_dirty: bool,
        phase: str,
    ) -> None:
        """Return GIT_TIMEOUT and errors == [f"Git timed out during {phase} for worktree 'dovo_t': {detail}"], write no event file, and leave HEAD unchanged for every phase before patch apply."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        detail = f"Git timed out after 30s ('git {' '.join(git_key)}')"
        fake = FakeGitRunner({git_key: GitError(ErrorCode.GIT_TIMEOUT, detail)})
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.apply("dovo_t", strategy=strategy, allow_dirty=allow_dirty)

        assert result.error_code == ErrorCode.GIT_TIMEOUT
        assert result.errors == [f"Git timed out during {phase} for worktree 'dovo_t': {detail}"]
        assert result.strategy == strategy
        assert not resolve_global_paths(None).logs_dir.exists()
        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            pytest.param(
                git_command_error(["apply"], 128, "", "fatal: corrupt patch"),
                ErrorCode.GIT_FAILED,
                id="git_failed",
            ),
            pytest.param(
                GitError(ErrorCode.GIT_BINARY_MISSING, "Git execution failed ('git apply'): git not found"),
                ErrorCode.GIT_BINARY_MISSING,
                id="git_binary_missing",
            ),
        ],
    )
    def test_git_failure_reports_its_code(
        self, worktree_workspace_paths: WorkspacePaths, error: GitError, code: ErrorCode
    ) -> None:
        """Return the git error's code and "Git failed during conflict check for worktree '<id>': <exc>" when apply_check raises it, writing no event file."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        fake = FakeGitRunner({("apply", "--check"): error})
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.apply("dovo_t")

        assert result.error_code == code
        assert result.errors == [f"Git failed during conflict check for worktree 'dovo_t': {error}"]
        assert result.diagnostics == []
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_nonzero_check_without_conflicting_paths_reports_git_failed(
        self, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        """Return GIT_FAILED, conflicting_files == [] and errors == ["Git failed during conflict check for worktree '<id>': error: No valid patches in input"] when ("apply","--check") exits 128 naming no path, and leave the worktree status unchanged."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        db = _repo(worktree_workspace_paths)
        before = db.get("dovo_t")
        assert before is not None
        fake = FakeGitRunner(
            {
                ("apply", "--check"): GitProcessOutput(
                    returncode=128, stdout="", stderr="error: No valid patches in input\n"
                )
            }
        )
        worktree = _worktree(worktree_workspace_paths, db, git=fake)

        result = worktree.apply("dovo_t")

        assert result.error_code == ErrorCode.GIT_FAILED
        assert result.conflicting_files == []
        assert result.errors == [
            "Git failed during conflict check for worktree 'dovo_t': error: No valid patches in input"
        ]
        after = db.get("dovo_t")
        assert after is not None
        assert after.status == before.status

    def test_fs_not_found_records_its_oserror_cause(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return FS_NOT_FOUND and diagnostics == [diagnostic_record(...)] with one error event {operation: worktree.apply, exception_type: FileNotFoundError} when apply_check raises GitError(FS_NOT_FOUND) chained to FileNotFoundError."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        try:
            raise FileNotFoundError("cwd vanished")
        except FileNotFoundError as cause:
            failure = GitError(ErrorCode.FS_NOT_FOUND, "working directory does not exist")
            failure.__cause__ = cause
        fake = FakeGitRunner({("apply", "--check"): failure})
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.apply("dovo_t")

        assert result.error_code == ErrorCode.FS_NOT_FOUND
        assert result.errors == [
            "Git failed during conflict check for worktree 'dovo_t': working directory does not exist"
        ]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.apply"
        assert event["exception_type"] == "FileNotFoundError"

    def test_nonzero_git_apply_reports_git_failed(self, worktree_workspace_paths: WorkspacePaths) -> None:
        """Return GIT_FAILED and errors == ["Git failed during patch apply for worktree '<id>': error: boom"] when ("apply","--binary") returns exit 1 with stderr "error: boom"."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_t")
        fake = FakeGitRunner({("apply", "--binary"): GitProcessOutput(returncode=1, stdout="", stderr="error: boom\n")})
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.apply("dovo_t")

        assert result.error_code == ErrorCode.GIT_FAILED
        assert result.errors == ["Git failed during patch apply for worktree 'dovo_t': error: boom"]


def _raise_boom(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a diff or repository call that fails with an unexpected RuntimeError("boom")."""
    raise RuntimeError("boom")


def _raise_type_error(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a diff call that fails with an unexpected TypeError("boom")."""
    raise TypeError("boom")


_GIT_KEYS: dict[str, tuple[str, ...]] = {
    "dirty_paths": ("status", "--porcelain=v1"),
    "apply_check": ("apply", "--check"),
    "apply": ("apply", "--binary"),
    "commit": ("--literal-pathspecs", "commit"),
}


def _create_conflicting_worktree(paths: WorkspacePaths, workspace: Path, worktree_id: str) -> None:
    """Create a worktree adding new_file.txt, then commit a different new_file.txt in the main repository."""
    _create_worktree_with_change(paths, worktree_id)
    (workspace / "new_file.txt").write_text("main change\n", encoding="utf-8")
    GitRunner().run(["add", "-A"], path=workspace)
    GitRunner().run(["commit", "-m", "main change"], path=workspace)


class WorktreePatchDiagnosticLogTests:
    @pytest.mark.parametrize(
        ("target", "strategy", "allow_dirty"),
        [
            pytest.param("diff_work_tree", WorktreeApplyStrategy.PATCH, False, id="diff_generation"),
            pytest.param("dirty_paths", WorktreeApplyStrategy.SQUASH, True, id="main_status_read"),
            pytest.param("apply_check", WorktreeApplyStrategy.PATCH, False, id="conflict_check"),
            pytest.param("apply", WorktreeApplyStrategy.PATCH, False, id="patch_application"),
            pytest.param("commit", WorktreeApplyStrategy.SQUASH, False, id="squash_commit"),
        ],
    )
    def test_apply_unexpected_failure_reports_internal_error_with_one_diagnostic(
        self,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
        strategy: WorktreeApplyStrategy,
        allow_dirty: bool,
    ) -> None:
        """Return INTERNAL_ERROR, errors == ["Unexpected error during worktree.apply: boom"], diagnostics == [diagnostic_record(...)] and one error event for worktree.apply."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_boom")
        fake = FakeGitRunner()
        if target == "diff_work_tree":
            monkeypatch.setattr("dovo.core.worktree.services.patch.diff_work_tree", _raise_type_error)
        else:
            fake.responses[_GIT_KEYS[target]] = TypeError("boom")
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = worktree.apply("dovo_boom", strategy=strategy, allow_dirty=allow_dirty)

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.apply: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.apply"
        assert event["context"] == {"worktree_id": "dovo_boom"}

    def test_diff_unexpected_failure_reports_internal_error_with_one_diagnostic(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return INTERNAL_ERROR, errors == ["Unexpected error during worktree.diff: boom"], diagnostics == [diagnostic_record(...)] and one error event {operation: worktree.diff, context: {"worktree_id": "dovo_diff"}} when diff_work_tree raises TypeError("boom")."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_diff")
        monkeypatch.setattr("dovo.core.worktree.services.patch.diff_work_tree", _raise_type_error)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.diff("dovo_diff")

        assert result.error_code == ErrorCode.INTERNAL_ERROR
        assert result.errors == ["Unexpected error during worktree.diff: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.diff"
        assert event["context"] == {"worktree_id": "dovo_diff"}

    def test_merged_status_write_failure_reports_warning_and_record(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return ok with warnings == ["Failed to update database status to 'merged' for worktree 'dovo_merge': boom"], diagnostics == [diagnostic_record(...)] and one error event for worktree.apply."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_merge")
        monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_merge")

        assert result.ok
        assert result.warnings == ["Failed to update database status to 'merged' for worktree 'dovo_merge': boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.apply"
        assert event["context"] == {"worktree_id": "dovo_merge"}

    def test_merged_status_write_failure_with_unwritable_log_keeps_warning(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return ok with the same warning and diagnostics == [] when record_exception returns None."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_merge")
        logs_dir = frozen_diagnostic_clock.parent.parent
        logs_dir.parent.mkdir(parents=True, exist_ok=True)
        logs_dir.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_merge")

        assert result.ok
        assert result.warnings == ["Failed to update database status to 'merged' for worktree 'dovo_merge': boom"]
        assert result.diagnostics == []

    @pytest.mark.parametrize("entrypoint", ["diff", "apply"])
    def test_reconcile_failure_records_debug_and_reports_worktree_missing(
        self,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        entrypoint: str,
    ) -> None:
        """Return WORKTREE_MISSING with the entrypoint's missing message and diagnostics == [], and write one debug event for worktree.<entrypoint>."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_gone")
        record = db.get("dovo_gone")
        assert record is not None
        shutil.rmtree(record.worktree_path)
        monkeypatch.setattr(WorktreesRepository, "reconcile_stale_active", _raise_boom)
        worktree = _worktree(worktree_workspace_paths, db, git=GitRunner())

        if entrypoint == "diff":
            diff_result = worktree.diff("dovo_gone")
            assert diff_result.error_code == ErrorCode.WORKTREE_MISSING
            assert diff_result.errors == ["Worktree 'dovo_gone' directory is missing on disk."]
            assert diff_result.diagnostics == []
        else:
            apply_result = worktree.apply("dovo_gone")
            assert apply_result.error_code == ErrorCode.WORKTREE_MISSING
            assert apply_result.errors == [
                "Worktree 'dovo_gone' directory is missing on disk; reconciled status to 'cleaned'."
            ]
            assert apply_result.diagnostics == []
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == f"worktree.{entrypoint}"
        assert event["context"] == {"worktree_id": "dovo_gone", "path": str(record.worktree_path)}

    def test_apply_conflict_reports_conflict_without_record(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return WORKTREE_APPLY_CONFLICT, conflicting_files == ["new_file.txt"], the two conflict remediations, and leave <DOVO_HOME>/logs absent."""
        _create_conflicting_worktree(worktree_workspace_paths, worktree_workspace, "dovo_conflict")
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_conflict")

        assert result.error_code == ErrorCode.WORKTREE_APPLY_CONFLICT
        assert result.conflicting_files == ["new_file.txt"]
        assert result.errors[0].startswith("Cannot apply worktree dovo_conflict: conflicts detected.")
        assert result.remediations == [
            Remediation(
                message="Inspect worktree differences with 'dovo worktree diff dovo_conflict'",
                command="dovo worktree diff dovo_conflict",
            ),
            Remediation(message="Resolve conflicts in the main workspace or worktree"),
        ]
        assert result.diagnostics == []
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_conflict_status_write_failure_records_debug_and_keeps_conflict(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return WORKTREE_APPLY_CONFLICT with diagnostics == [] and write one debug event for worktree.apply."""
        _create_conflicting_worktree(worktree_workspace_paths, worktree_workspace, "dovo_conflict")
        monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        worktree = _worktree(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = worktree.apply("dovo_conflict")

        assert result.error_code == ErrorCode.WORKTREE_APPLY_CONFLICT
        assert result.diagnostics == []
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.apply"
        assert event["context"] == {"worktree_id": "dovo_conflict"}
