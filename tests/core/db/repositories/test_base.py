"""Contract tests for BaseRepository's lazy project_id resolution and transaction handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree.core.db.repositories.runs import RunsRepository
from worktree.core.db.repositories.sandboxes import SandboxesRepository
from worktree.core.project.services.identity import generate_project_identity, save_project_identity


class BaseRepositoryTests:
    """Contract tests for BaseRepository.project_id lazy resolution and caching."""

    def test_project_id_missing_and_unresolvable_raises_value_error(self, tmp_path: Path) -> None:
        """[tier-1/unit] BaseRepository.project_id: no persisted project.json and no explicit override raises ValueError('project_id must be provided')."""
        repo = RunsRepository(path=tmp_path)

        with pytest.raises(ValueError, match="project_id must be provided"):
            _ = repo.project_id

    def test_project_id_resolved_from_persisted_identity(self, tmp_path: Path) -> None:
        """[tier-1/integration] BaseRepository.project_id: persisted project.json id 'proj-abc' resolves RunsRepository(path=...).project_id == 'proj-abc'."""
        dot_worktree = tmp_path / ".worktree"
        dot_worktree.mkdir(parents=True, exist_ok=True)
        save_project_identity(dot_worktree / "project.json", generate_project_identity(project_id="proj-abc"))

        repo = RunsRepository(path=tmp_path)

        assert repo.project_id == "proj-abc"

    def test_project_id_explicit_override_bypasses_resolution(self, tmp_path: Path) -> None:
        """[tier-1/unit] BaseRepository.project_id: explicit project_id='explicit' constructor override wins over a different persisted project.json."""
        dot_worktree = tmp_path / ".worktree"
        dot_worktree.mkdir(parents=True, exist_ok=True)
        save_project_identity(dot_worktree / "project.json", generate_project_identity(project_id="proj-other"))

        repo = RunsRepository(path=tmp_path, project_id="explicit")

        assert repo.project_id == "explicit"


class BaseRepositoryCommitRollbackTests:
    """[tier-1/integration] BaseRepository._commit: constraint-violation rollback, surfaced through SandboxesRepository.create."""

    def test_duplicate_primary_key_rolls_back_and_raises_value_error_with_conflict_message(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] SandboxesRepository.create: creating a second row with the same id raises ValueError with the repository's conflict_message, and the original row's data is unaffected (transaction rolled back, not partially applied)."""
        repo = SandboxesRepository(path=tmp_path, project_id="proj-commit")
        repo.create(
            id="sbx_dup",
            branch_name="worktree/sandbox-sbx_dup",
            base_commit="abc123",
            sandbox_path=tmp_path / "sbx_dup",
        )

        with pytest.raises(ValueError, match="Sandbox with id 'sbx_dup' already exists"):
            repo.create(
                id="sbx_dup",
                branch_name="worktree/sandbox-other",
                base_commit="def456",
                sandbox_path=tmp_path / "other",
            )

        stored = repo.get("sbx_dup")
        assert stored is not None
        assert stored.branch_name == "worktree/sandbox-sbx_dup"
        assert stored.base_commit == "abc123"
