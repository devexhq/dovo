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
from worktree.core.sandbox.services.patch import SandboxPatch


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


class SandboxSquashApplyTests:
    """Integration tests verifying squash apply strategy and branch cleanup."""

    def test_squash_apply_collapses_multiple_commits_into_single_commit(
        self,
        sandbox_workspace: Path,
        sandbox_workspace_paths: WorkspacePaths,
    ) -> None:
        """Squash strategy collapses multiple intermediate sandbox commits into single commit with message."""
        db = SandboxesRepository(
            db_path=sandbox_workspace_paths.database_file, project_id=sandbox_workspace_paths.project_id
        )
        lifecycle = SandboxLifecycle(sandbox_workspace_paths, db)
        patch_service = SandboxPatch(sandbox_workspace_paths, db, lifecycle=lifecycle)

        head_before = GitRunner.rev_parse(sandbox_workspace, rev="HEAD")

        create_result = lifecycle.create(session_id="sbx_squash")
        assert create_result.status == SandboxCreateStatus.OK

        sandbox_dir = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_squash"

        (sandbox_dir / "feature1.py").write_text("feature 1\n", encoding="utf-8")
        GitRunner.add_all(sandbox_dir)
        GitRunner.commit(sandbox_dir, "Commit 1 in sandbox")

        (sandbox_dir / "feature2.py").write_text("feature 2\n", encoding="utf-8")
        GitRunner.add_all(sandbox_dir)
        GitRunner.commit(sandbox_dir, "Commit 2 in sandbox")

        result = patch_service.apply(
            "sbx_squash",
            strategy=SandboxApplyStrategy.SQUASH,
            message="Squash sandbox feature",
        )

        assert result.status == SandboxApplyStatus.OK
        assert result.sandbox_id == "sbx_squash"
        assert result.strategy == SandboxApplyStrategy.SQUASH
        assert result.touched_files == ["feature1.py", "feature2.py"]
        assert result.conflicting_files == []
        assert result.cleaned_up is False
        assert result.commit_sha is not None
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert GitRunner.rev_parse(sandbox_workspace, rev="HEAD") == result.commit_sha
        assert GitRunner.run(["log", "-1", "--format=%B"], sandbox_workspace).strip() == "Squash sandbox feature"
        assert GitRunner.rev_parse(sandbox_workspace, rev="HEAD~1") == head_before
        assert (sandbox_workspace / "feature1.py").read_text(encoding="utf-8") == "feature 1\n"
        assert (sandbox_workspace / "feature2.py").read_text(encoding="utf-8") == "feature 2\n"

    def test_squash_apply_deletes_sandbox_branch_and_marks_applied(
        self,
        sandbox_workspace: Path,
        sandbox_workspace_paths: WorkspacePaths,
    ) -> None:
        """Squash apply with delete=True deletes sandbox branch, worktree directory, and marks sandbox merged."""
        db = SandboxesRepository(
            db_path=sandbox_workspace_paths.database_file, project_id=sandbox_workspace_paths.project_id
        )
        lifecycle = SandboxLifecycle(sandbox_workspace_paths, db)
        patch_service = SandboxPatch(sandbox_workspace_paths, db, lifecycle=lifecycle)

        initial_commit = GitRunner.rev_parse(sandbox_workspace, rev="HEAD")

        create_result = lifecycle.create(session_id="sbx_squash_del")
        assert create_result.status == SandboxCreateStatus.OK

        sandbox_dir = sandbox_workspace / ".worktree" / "sandboxes" / "sbx_squash_del"

        (sandbox_dir / "feature.py").write_text("squash feature\n", encoding="utf-8")
        GitRunner.add_all(sandbox_dir)
        GitRunner.commit(sandbox_dir, "Add feature in sandbox")

        result = patch_service.apply(
            "sbx_squash_del",
            strategy=SandboxApplyStrategy.SQUASH,
            delete=True,
            message="Squash and cleanup",
        )

        assert result.status == SandboxApplyStatus.OK
        assert result.sandbox_id == "sbx_squash_del"
        assert result.strategy == SandboxApplyStrategy.SQUASH
        assert result.touched_files == ["feature.py"]
        assert result.conflicting_files == []
        assert result.cleaned_up is True
        assert result.commit_sha is not None
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

        assert "worktree/sandbox-sbx_squash_del" not in GitRunner.list_branches(sandbox_workspace)
        assert not (sandbox_workspace / ".worktree" / "sandboxes" / "sbx_squash_del").exists()

        record = db.get("sbx_squash_del")
        assert record is not None
        assert record.id == "sbx_squash_del"
        assert record.name is None
        assert record.branch_name == "worktree/sandbox-sbx_squash_del"
        assert record.base_commit == initial_commit
        assert record.sandbox_path == (sandbox_workspace / ".worktree" / "sandboxes" / "sbx_squash_del").resolve()
        assert record.status == SandboxStatus.MERGED
        assert record.created_at is not None
        assert record.updated_at is not None
