"""Contract tests for BaseRepository's explicit db_path/project_id and transaction handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.db.repositories.sessions import SessionsRepository
from dovo.core.db.repositories.worktrees import WorktreesRepository


class BaseRepositoryTests:
    """Contract tests for BaseRepository.db_path/project_id explicit-value contracts."""

    def test_project_id_missing_raises_value_error(self) -> None:
        repo = SessionsRepository()

        with pytest.raises(ValueError, match="project_id must be provided"):
            _ = repo.project_id

    def test_project_id_returns_explicitly_provided_value(self) -> None:
        repo = SessionsRepository(project_id="explicit")

        assert repo.project_id == "explicit"

    def test_db_path_missing_raises_value_error(self) -> None:
        repo = SessionsRepository()

        with pytest.raises(ValueError, match="db_path must be provided"):
            _ = repo.db_path

    def test_db_path_returns_explicitly_provided_value(self, tmp_path: Path) -> None:
        database_file = tmp_path / "dovo.db"
        repo = SessionsRepository(db_path=database_file)

        assert repo.db_path == database_file


class BaseRepositoryCommitRollbackTests:
    def test_duplicate_primary_key_rolls_back_and_raises_value_error_with_conflict_message(
        self, tmp_path: Path
    ) -> None:
        repo = WorktreesRepository(db_path=tmp_path / "dovo.db", project_id="proj-commit")
        repo.create(
            id="dovo_dup",
            branch_name="dovo/dovo_dup",
            base_commit="abc123",
            worktree_path=tmp_path / "dovo_dup",
        )

        with pytest.raises(ValueError, match="Worktree with id 'dovo_dup' already exists"):
            repo.create(
                id="dovo_dup",
                branch_name="dovo/other",
                base_commit="def456",
                worktree_path=tmp_path / "other",
            )

        stored = repo.get("dovo_dup")
        assert stored is not None
        assert stored.branch_name == "dovo/dovo_dup"
        assert stored.base_commit == "abc123"
