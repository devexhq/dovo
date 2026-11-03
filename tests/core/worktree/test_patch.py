from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.db import WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.worktree.models import WorktreeApplyStrategy
from dovo.core.worktree.worktree import Worktree
from tests.harness.builders import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_record
from tests.harness.fakes import FakeGitRunner, fixed_clock, fixed_new_id


@pytest.fixture
def worktree_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "worktree_ws").with_git().with_database().build()


@pytest.fixture
def worktree_workspace_paths(
    worktree_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's worktree workspace."""
    return workspace_paths_factory(worktree_workspace, None)


class WorktreeApplyRollbackTests:
    """Integration tests verifying working tree rollback on conflict during patch apply."""

    def test_worktree_patch_apply_rolls_back_partial_changes_on_conflict(
        self,
        worktree_workspace: Path,
        worktree_workspace_paths: WorkspacePaths,
    ) -> None:
        db = WorktreesRepository(
            db_path=worktree_workspace_paths.database_file, project_id=worktree_workspace_paths.project_id
        )
        worktree = Worktree(worktree_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id)

        (worktree_workspace / "target.py").write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=worktree_workspace)
        GitRunner().run(["commit", "-m", "Add target.py"], path=worktree_workspace)
        initial_commit = GitRunner().rev_parse(worktree_workspace, rev="HEAD")

        create_result = Worktree(
            worktree_workspace_paths, db=db, git=GitRunner(), clock=fixed_clock, new_id=fixed_new_id
        ).create(session_id="dovo_conflict")
        assert create_result.ok

        worktree_dir = worktree_workspace / ".dovo" / "worktrees" / "dovo_conflict"
        (worktree_dir / "target.py").write_text("line 1\nworktree edit\nline 3\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=worktree_dir)
        GitRunner().run(["commit", "-m", "Worktree change to target.py"], path=worktree_dir)

        (worktree_workspace / "target.py").write_text("line 1\nconflicting main edit\nline 3\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=worktree_workspace)
        GitRunner().run(["commit", "-m", "Main change causing conflict"], path=worktree_workspace)

        initial_tree = (worktree_workspace / "target.py").read_text(encoding="utf-8")

        result = worktree.apply("dovo_conflict")

        assert result.error_code == ErrorCode.WORKTREE_APPLY_CONFLICT
        assert result.worktree_id == "dovo_conflict"
        assert result.strategy == WorktreeApplyStrategy.PATCH
        assert result.touched_files == []
        assert result.conflicting_files == ["target.py"]
        assert result.cleaned_up is False
        assert result.commit_sha is None
        assert result.errors == [
            "Cannot apply worktree dovo_conflict: conflicts detected.\nConflicting files:\n  • target.py"
        ]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(
                message="Inspect worktree differences with 'dovo worktree diff dovo_conflict'",
                command="dovo worktree diff dovo_conflict",
            ),
            Remediation(message="Resolve conflicts in the main workspace or worktree"),
        ]

        assert (worktree_workspace / "target.py").read_text(encoding="utf-8") == initial_tree

        record = db.get("dovo_conflict")
        assert record is not None
        assert record.id == "dovo_conflict"
        assert record.name is None
        assert record.branch_name == "dovo/dovo_conflict"
        assert record.base_commit == initial_commit
        assert record.worktree_path == (worktree_workspace / ".dovo" / "worktrees" / "dovo_conflict").resolve()
        assert record.status == WorktreeStatus.CONFLICT


class WorktreeApplyDeleteDiagnosticsTests:
    def test_apply_delete_copies_cleanup_warnings_and_diagnostics(
        self, worktree_workspace: Path, worktree_workspace_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Return ok, cleaned_up True, warnings == ["Failed to delete branch 'dovo/<id>': boom"] and diagnostics == [diagnostic_record(...)] from apply(delete=True) when git branch -D raises RuntimeError("boom")."""
        git = FakeGitRunner()
        worktree = Worktree(worktree_workspace_paths, git=git, clock=fixed_clock, new_id=fixed_new_id)
        created = worktree.create(session_id="dovo_apply_delete")
        assert created.session is not None
        (created.session.worktree_path / "feature.py").write_text("feature\n", encoding="utf-8")
        GitRunner().run(["add", "-A"], path=created.session.worktree_path)
        GitRunner().run(["commit", "-m", "Add feature"], path=created.session.worktree_path)
        git.responses[("branch", "-D")] = RuntimeError("boom")

        result = worktree.apply("dovo_apply_delete", delete=True)

        assert result.ok
        assert result.cleaned_up is True
        assert result.warnings == ["Failed to delete branch 'dovo/dovo_apply_delete': boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
