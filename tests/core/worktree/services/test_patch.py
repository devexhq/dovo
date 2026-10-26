"""Contract tests for worktree conflict extraction, diff inspection, and patch application."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.common.filesystem.services import resolve_global_paths
from dovo.common.models import Remediation
from dovo.core.db import WorktreesRepository
from dovo.core.db.models import WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeApplyStatus, WorktreeApplyStrategy, WorktreeDiffStatus
from dovo.core.worktree.services.lifecycle import WorktreeLifecycle
from dovo.core.worktree.services.patch import WorktreePatch, extract_conflicts
from dovo.core.worktree.worktree import Worktree
from tests.harness import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
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


def _patch(paths: WorkspacePaths, db: WorktreesRepository, *, git: GitRunner) -> WorktreePatch:
    """Build a WorktreePatch over db whose lifecycle runs git through the same runner."""
    lifecycle = WorktreeLifecycle(paths, db, git=git, clock=fixed_clock, new_id=fixed_new_id)
    return WorktreePatch(paths, db, git=git, lifecycle=lifecycle)


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
    def test_unknown_worktree_id_returns_not_found(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.diff("missing_worktree")

        assert result.status == WorktreeDiffStatus.NOT_FOUND
        assert "missing_worktree" in result.errors[0]

    def test_worktree_directory_missing_on_disk_returns_not_found(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_gone")
        record = db.get("dovo_gone")
        assert record is not None
        import shutil

        shutil.rmtree(record.worktree_path)
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.diff("dovo_gone")

        assert result.status == WorktreeDiffStatus.NOT_FOUND

    def test_worktree_with_no_changes_returns_empty_diff(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        create_result = Worktree(
            worktree_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(session_id="dovo_empty")
        assert create_result.session is not None
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.diff("dovo_empty")

        assert result.status == WorktreeDiffStatus.EMPTY_DIFF

    def test_worktree_with_committed_change_returns_ok_with_touched_files(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_diffme")
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.diff("dovo_diffme")

        assert result.status == WorktreeDiffStatus.OK
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
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.diff("dovo_stat", stat=stat)

        assert result.status == WorktreeDiffStatus.OK
        assert "diff --git" in result.diff_text
        assert bool(result.stat_text) is expect_stat_text
        if expect_stat_text:
            assert "new_file.txt" in result.stat_text


class WorktreePatchApplyTests:
    def test_unknown_worktree_id_returns_not_found(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("missing_worktree")

        assert result.status == WorktreeApplyStatus.NOT_FOUND

    def test_already_merged_worktree_returns_already_merged_without_reapplying(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_merged")
        db.update_status("dovo_merged", WorktreeStatus.MERGED)
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_merged")

        assert result.status == WorktreeApplyStatus.ALREADY_MERGED

    @pytest.mark.parametrize(
        "strategy",
        [
            pytest.param(WorktreeApplyStrategy.PATCH, id="patch"),
            pytest.param(WorktreeApplyStrategy.SQUASH, id="squash"),
        ],
    )
    def test_dirty_main_repo_without_allow_dirty_returns_main_repo_dirty(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, strategy: WorktreeApplyStrategy
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_dirty_main")
        (worktree_workspace / "uncommitted.txt").write_text("wip", encoding="utf-8")
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_dirty_main", strategy=strategy)

        assert result.status == WorktreeApplyStatus.MAIN_REPO_DIRTY
        assert result.errors == ["Cannot apply worktree dovo_dirty_main: main repository has uncommitted changes."]

    def test_worktree_with_no_changes_returns_empty_diff(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        create_result = Worktree(
            worktree_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(session_id="dovo_empty_apply")
        assert create_result.session is not None
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_empty_apply")

        assert result.status == WorktreeApplyStatus.EMPTY_DIFF

    def test_dry_run_reports_ok_without_modifying_main_workspace(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_dry_run")
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_dry_run", dry_run=True)

        assert result.status == WorktreeApplyStatus.OK
        assert any("dry run" in w.lower() for w in result.warnings)
        assert not (worktree_workspace / "new_file.txt").exists()

    def test_patch_strategy_applies_changes_and_marks_worktree_merged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_apply_patch")
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_apply_patch", strategy=WorktreeApplyStrategy.PATCH)

        assert result.status == WorktreeApplyStatus.OK
        assert (worktree_workspace / "new_file.txt").read_text(encoding="utf-8") == "worktree change\n"
        record = db.get("dovo_apply_patch")
        assert record is not None
        assert record.status == WorktreeStatus.MERGED

    def test_squash_strategy_creates_single_commit_with_default_message(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_squash")
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_squash", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.status == WorktreeApplyStatus.OK
        assert result.commit_sha is not None
        log_message = GitRunner().run(["log", "-1", "--format=%s"], path=worktree_workspace).strip()
        assert log_message == "dovo: apply changes from worktree dovo_squash"


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
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_scope", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.status == WorktreeApplyStatus.OK
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
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_unrelated", strategy=WorktreeApplyStrategy.SQUASH, allow_dirty=True)

        assert result.status == WorktreeApplyStatus.OK
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
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        result = patch.apply("dovo_overlap", strategy=WorktreeApplyStrategy.SQUASH, allow_dirty=True, dry_run=dry_run)

        assert result.status == WorktreeApplyStatus.MAIN_REPO_DIRTY
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

    def test_failed_squash_commit_returns_git_failed_and_creates_no_commit(
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
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_hook", strategy=WorktreeApplyStrategy.SQUASH, allow_dirty=True)

        assert result.status == WorktreeApplyStatus.GIT_FAILED
        assert result.errors[0].startswith("Git squash commit failed: ")
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
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_rename", strategy=WorktreeApplyStrategy.SQUASH)

        assert result.status == WorktreeApplyStatus.OK
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
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.diff("dovo_neutral_diff", stat=stat)

        assert result.status == WorktreeDiffStatus.OK
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
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_neutral_apply", dry_run=dry_run)

        assert result.status == WorktreeApplyStatus.OK
        assert result.touched_files == _NEUTRALITY_FILES
        assert snapshot_index(worktree_path) == before


class WorktreePatchDiffFailureTests:
    def test_missing_worktree_index_returns_git_failed_and_leaves_no_temp_index(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree_path = _remove_worktree_index(worktree_workspace_paths, "dovo_noindex")
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.diff("dovo_noindex")

        assert result.status == WorktreeDiffStatus.GIT_FAILED
        assert result.errors[0].startswith("Failed to generate diff for worktree 'dovo_noindex': ")
        assert temp_index_files(worktree_path) == []

    def test_apply_with_missing_worktree_index_returns_git_failed_and_leaves_main_repo_unchanged(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths
    ) -> None:
        worktree_path = _remove_worktree_index(worktree_workspace_paths, "dovo_noindex_apply")
        head_before = GitRunner().rev_parse(worktree_workspace, rev="HEAD")
        status_before = GitRunner().status_porcelain(worktree_workspace)
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_noindex_apply")

        assert result.status == WorktreeApplyStatus.GIT_FAILED
        assert result.errors[0].startswith("Failed to generate diff from worktree: ")
        assert GitRunner().rev_parse(worktree_workspace, rev="HEAD") == head_before
        assert GitRunner().status_porcelain(worktree_workspace) == status_before
        assert temp_index_files(worktree_path) == []


def _raise_boom(*args: object, **kwargs: object) -> NoReturn:
    """Stand in for a diff or repository call that fails with an unexpected RuntimeError("boom")."""
    raise RuntimeError("boom")


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
        ("target", "strategy", "allow_dirty", "prefix", "context_has_path"),
        [
            pytest.param(
                "diff_work_tree",
                WorktreeApplyStrategy.PATCH,
                False,
                "Failed to generate diff from worktree: boom",
                True,
                id="diff_generation",
            ),
            pytest.param(
                "dirty_paths",
                WorktreeApplyStrategy.SQUASH,
                True,
                "Failed to read main repository status: boom",
                False,
                id="main_status_read",
            ),
            pytest.param(
                "apply_check",
                WorktreeApplyStrategy.PATCH,
                False,
                "Git failure during conflict check: boom",
                False,
                id="conflict_check",
            ),
            pytest.param(
                "apply", WorktreeApplyStrategy.PATCH, False, "Git apply failed: boom", False, id="patch_application"
            ),
            pytest.param(
                "commit",
                WorktreeApplyStrategy.SQUASH,
                False,
                "Git squash commit failed: boom",
                False,
                id="squash_commit",
            ),
        ],
    )
    def test_apply_unexpected_failure_records_error_and_appends_hint(
        self,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: str,
        strategy: WorktreeApplyStrategy,
        allow_dirty: bool,
        prefix: str,
        context_has_path: bool,
    ) -> None:
        """Return git_failed with errors == [f"{prefix}\nDetails logged: ... (event exc_3f9a1c07b2de)"] and write one error record for worktree.apply with context worktree_id, plus path for diff generation."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_boom")
        record = _repo(worktree_workspace_paths).get("dovo_boom")
        assert record is not None
        expected_context = {"worktree_id": "dovo_boom"}
        if context_has_path:
            expected_context["path"] = str(record.worktree_path)
        fake = FakeGitRunner()
        if target == "diff_work_tree":
            monkeypatch.setattr("dovo.core.worktree.services.patch.diff_work_tree", _raise_boom)
        else:
            fake.responses[_GIT_KEYS[target]] = RuntimeError("boom")
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=fake)

        result = patch.apply("dovo_boom", strategy=strategy, allow_dirty=allow_dirty)

        assert result.status == WorktreeApplyStatus.GIT_FAILED
        assert result.errors == [f"{prefix}{diagnostic_hint(frozen_diagnostic_clock)}"]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.apply"
        assert event["context"] == expected_context

    def test_diff_unexpected_failure_records_error_and_appends_hint(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return git_failed with errors == ["Failed to generate diff for worktree 'dovo_diff': boom\nDetails logged: ..."] and one error record for worktree.diff."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_diff")
        record = _repo(worktree_workspace_paths).get("dovo_diff")
        assert record is not None
        monkeypatch.setattr("dovo.core.worktree.services.patch.diff_work_tree", _raise_boom)
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.diff("dovo_diff")

        assert result.status == WorktreeDiffStatus.GIT_FAILED
        assert result.errors == [
            f"Failed to generate diff for worktree 'dovo_diff': boom{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.diff"
        assert event["context"] == {"worktree_id": "dovo_diff", "path": str(record.worktree_path)}

    def test_merged_status_write_failure_hints_warning(
        self, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return ok with warnings == ["Failed to update database status to 'merged' for worktree 'dovo_merge': boom\nDetails logged: ..."] when update_status raises RuntimeError."""
        _create_worktree_with_change(worktree_workspace_paths, "dovo_merge")
        monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_merge")

        assert result.status == WorktreeApplyStatus.OK
        assert result.warnings == [
            "Failed to update database status to 'merged' for worktree 'dovo_merge': boom"
            f"{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "worktree.apply"
        assert event["context"] == {"worktree_id": "dovo_merge"}

    @pytest.mark.parametrize("entrypoint", ["diff", "apply"])
    def test_reconcile_failure_records_debug_and_keeps_not_found(
        self,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
        entrypoint: str,
    ) -> None:
        """Return not_found with the pre-change unhinted error when the worktree directory is gone and reconcile_stale_active raises, and write one debug record for worktree.<entrypoint>."""
        db = _repo(worktree_workspace_paths)
        _create_worktree_with_change(worktree_workspace_paths, "dovo_gone")
        record = db.get("dovo_gone")
        assert record is not None
        shutil.rmtree(record.worktree_path)
        monkeypatch.setattr(WorktreesRepository, "reconcile_stale_active", _raise_boom)
        patch = _patch(worktree_workspace_paths, db, git=GitRunner())

        if entrypoint == "diff":
            diff_result = patch.diff("dovo_gone")
            assert diff_result.status == WorktreeDiffStatus.NOT_FOUND
            assert diff_result.errors == ["Worktree 'dovo_gone' directory is missing on disk."]
        else:
            apply_result = patch.apply("dovo_gone")
            assert apply_result.status == WorktreeApplyStatus.NOT_FOUND
            assert apply_result.errors == [
                "Worktree 'dovo_gone' directory is missing on disk; reconciled status to 'cleaned'."
            ]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == f"worktree.{entrypoint}"
        assert event["context"] == {"worktree_id": "dovo_gone", "path": str(record.worktree_path)}

    def test_apply_conflict_writes_no_record(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return conflict with an unhinted error for a conflicting patch and leave <DOVO_HOME>/logs absent."""
        _create_conflicting_worktree(worktree_workspace_paths, worktree_workspace, "dovo_conflict")
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_conflict")

        assert result.status == WorktreeApplyStatus.CONFLICT
        assert result.errors[0].startswith("Cannot apply worktree dovo_conflict: conflicts detected.")
        assert "Details logged:" not in result.errors[0]
        assert not resolve_global_paths(None).logs_dir.exists()

    def test_conflict_status_write_failure_records_debug_and_keeps_conflict(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
        frozen_diagnostic_clock: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return conflict with the unhinted error when update_status raises during conflict reporting, and write one debug record for worktree.apply."""
        _create_conflicting_worktree(worktree_workspace_paths, worktree_workspace, "dovo_conflict")
        monkeypatch.setattr(WorktreesRepository, "update_status", _raise_boom)
        patch = _patch(worktree_workspace_paths, _repo(worktree_workspace_paths), git=GitRunner())

        result = patch.apply("dovo_conflict")

        assert result.status == WorktreeApplyStatus.CONFLICT
        assert result.errors[0].startswith("Cannot apply worktree dovo_conflict: conflicts detected.")
        assert "Details logged:" not in result.errors[0]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "debug"
        assert event["operation"] == "worktree.apply"
        assert event["context"] == {"worktree_id": "dovo_conflict"}
