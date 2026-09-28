from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from worktree.common.filesystem import WorkspacePaths
from worktree.core.db import SandboxesRepository, SandboxStatus
from worktree.core.git.runner import GitRunner
from worktree.core.sandbox.models import (
    SandboxApplyStatus,
    SandboxApplyStrategy,
    SandboxCreateStatus,
)
from worktree.core.sandbox.services.lifecycle import SandboxLifecycle
from worktree.core.sandbox.services.patch import SandboxPatch, extract_conflicts


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Create a fully initialized workspace with Git and SQLite DB."""
    return WorkspaceBuilder(tmp_path / "sandbox_ws").with_git().with_database().build()


@pytest.fixture
def sandbox_workspace_paths(
    sandbox_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the command-scoped paths for this module's sandbox workspace."""
    return workspace_paths_factory(sandbox_workspace, None)


class SandboxConflictExtractionTests:
    """Integration tests verifying git apply stderr line parsing."""

    @pytest.mark.parametrize(
        ("stderr_input", "expected_conflicts"),
        [
            pytest.param(
                "error: patch failed: src/main.py:12\nerror: src/main.py: patch does not apply",
                ["src/main.py"],
                id="patch_failed_and_does_not_apply",
            ),
            pytest.param(
                "error: cannot apply binary patch to 'assets/logo.png' without full index line",
                ["assets/logo.png"],
                id="binary_patch_failure",
            ),
            pytest.param(
                "error: new_file.txt: already exists in working directory\nerror: deleted_file.txt: does not exist in index",
                ["deleted_file.txt", "new_file.txt"],
                id="already_exists_and_not_in_index",
            ),
            pytest.param(
                "error: patch failed: a.py:1\nerror: patch failed: b.py:5\nerror: a.py: patch does not apply",
                ["a.py", "b.py"],
                id="multiline_deduplication",
            ),
            pytest.param(
                "",
                [],
                id="empty_stderr",
            ),
        ],
    )
    def test_extract_conflicts_parses_git_apply_stderr_lines(
        self,
        stderr_input: str,
        expected_conflicts: list[str],
    ) -> None:
        """Extract conflicting file paths from varied git apply stderr output lines."""
        assert extract_conflicts(stderr_input) == expected_conflicts


class SandboxApplyRollbackTests:
    """Integration tests verifying working tree rollback on conflict during patch apply."""

    def test_sandbox_patch_apply_rolls_back_partial_changes_on_conflict(
        self,
        sandbox_workspace: Path,
        sandbox_workspace_paths: WorkspacePaths,
    ) -> None:
        """Conflict during patch application rolls back partial changes and preserves working tree."""
        db = SandboxesRepository(
            db_path=sandbox_workspace_paths.database_file, project_id=sandbox_workspace_paths.project_id
        )
        lifecycle = SandboxLifecycle(sandbox_workspace_paths, db)
        patch_service = SandboxPatch(sandbox_workspace_paths, db, lifecycle=lifecycle)

        (sandbox_workspace / "target.py").write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
        GitRunner.add_all(sandbox_workspace)
        GitRunner.commit(sandbox_workspace, "Add target.py")
        initial_commit = GitRunner.rev_parse(sandbox_workspace, rev="HEAD")

        create_result = lifecycle.create(session_id="sbx_conflict")
        assert create_result.status == SandboxCreateStatus.OK

        sandbox_dir = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_conflict"
        (sandbox_dir / "target.py").write_text("line 1\nsandbox edit\nline 3\n", encoding="utf-8")
        GitRunner.add_all(sandbox_dir)
        GitRunner.commit(sandbox_dir, "Sandbox change to target.py")

        (sandbox_workspace / "target.py").write_text("line 1\nconflicting main edit\nline 3\n", encoding="utf-8")
        GitRunner.add_all(sandbox_workspace)
        GitRunner.commit(sandbox_workspace, "Main change causing conflict")

        initial_tree = (sandbox_workspace / "target.py").read_text(encoding="utf-8")

        result = patch_service.apply("sbx_conflict")

        assert result.status == SandboxApplyStatus.CONFLICT
        assert result.sandbox_id == "sbx_conflict"
        assert result.strategy == SandboxApplyStrategy.PATCH
        assert result.touched_files == []
        assert result.conflicting_files == ["target.py"]
        assert result.cleaned_up is False
        assert result.commit_sha is None
        assert result.errors == [
            "Cannot apply sandbox sbx_conflict: conflicts detected.\nConflicting files:\n  • target.py"
        ]
        assert result.warnings == []
        assert result.fixes == [
            "Inspect sandbox differences with `wt sandbox diff sbx_conflict`",
            "Resolve conflicts in the main workspace or sandbox worktree",
        ]

        assert (sandbox_workspace / "target.py").read_text(encoding="utf-8") == initial_tree

        record = db.get("sbx_conflict")
        assert record is not None
        assert record.id == "sbx_conflict"
        assert record.name is None
        assert record.branch_name == "worktree/sandbox-sbx_conflict"
        assert record.base_commit == initial_commit
        assert record.sandbox_path == (sandbox_workspace / ".worktree" / "sandboxes" / "sbx_conflict").resolve()
        assert record.status == SandboxStatus.CONFLICT
