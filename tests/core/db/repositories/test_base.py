"""Contract tests for BaseRepository's explicit db_path/project_id and transaction handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree.core.db.repositories.runs import RunsRepository
from worktree.core.db.repositories.sandboxes import SandboxesRepository


class BaseRepositoryTests:
    """Contract tests for BaseRepository.db_path/project_id explicit-value contracts."""

    def test_project_id_missing_raises_value_error(self) -> None:
        """[tier-1/unit] BaseRepository.project_id: no explicit project_id supplied at construction raises ValueError('project_id must be provided')."""
        repo = RunsRepository()

        with pytest.raises(ValueError, match="project_id must be provided"):
            _ = repo.project_id

    def test_project_id_returns_explicitly_provided_value(self) -> None:
        """[tier-1/unit] BaseRepository.project_id: returns the exact value supplied at construction."""
        repo = RunsRepository(project_id="explicit")

        assert repo.project_id == "explicit"

    def test_db_path_missing_raises_value_error(self) -> None:
        """[tier-1/unit] BaseRepository.db_path: no explicit db_path supplied at construction raises ValueError('db_path must be provided')."""
        repo = RunsRepository()

        with pytest.raises(ValueError, match="db_path must be provided"):
            _ = repo.db_path

    def test_db_path_returns_explicitly_provided_value(self, tmp_path: Path) -> None:
        """[tier-1/unit] BaseRepository.db_path: returns the exact value supplied at construction."""
        database_file = tmp_path / "worktree.db"
        repo = RunsRepository(db_path=database_file)

        assert repo.db_path == database_file


class BaseRepositoryCommitRollbackTests:
    """[tier-1/integration] BaseRepository._commit: constraint-violation rollback, surfaced through SandboxesRepository.create."""

    def test_duplicate_primary_key_rolls_back_and_raises_value_error_with_conflict_message(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] SandboxesRepository.create: creating a second row with the same id raises ValueError with the repository's conflict_message, and the original row's data is unaffected (transaction rolled back, not partially applied)."""
        repo = SandboxesRepository(db_path=tmp_path / "worktree.db", project_id="proj-commit")
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
