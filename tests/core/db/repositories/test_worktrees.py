"""Contract tests for WorktreesRepository project_id scoping."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.repositories.worktrees import WorktreesRepository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a freshly migrated, isolated SQLite database file."""
    path = tmp_path / "dovo.db"
    init_database(path)
    return path


@pytest.fixture
def db_engine(db_path: Path) -> Engine:
    """Engine bound to the migrated database file, shared across scoped repositories."""
    return get_engine(db_path)


class WorktreesRepositoryTests:
    """Contract tests proving WorktreesRepository queries never cross project_id boundaries."""

    def test_list_scopes_to_repository_project_id(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] WorktreesRepository.list: worktree created under project_id='proj-a' is absent from a project_id='proj-b' repository's list()."""
        repo_a = WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(id="dovo_a", branch_name="dovo/dovo_a", base_commit="deadbeef", worktree_path="/tmp/dovo_a")
        repo_b.create(id="dovo_b", branch_name="dovo/dovo_b", base_commit="deadbeef", worktree_path="/tmp/dovo_b")

        assert [record.id for record in repo_a.list()] == ["dovo_a"]
        assert [record.id for record in repo_b.list()] == ["dovo_b"]

    def test_get_returns_none_for_worktree_in_different_project(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] WorktreesRepository.get: worktree created under project_id='proj-a' returns None when fetched by a project_id='proj-b' repository."""
        repo_a = WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = WorktreesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(id="dovo_a", branch_name="dovo/dovo_a", base_commit="deadbeef", worktree_path="/tmp/dovo_a")

        assert repo_b.get("dovo_a") is None
        assert repo_a.get("dovo_a") is not None
