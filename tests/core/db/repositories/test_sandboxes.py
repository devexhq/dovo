"""Contract tests for SandboxesRepository project_id scoping."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from worktree.core.db.connection import get_engine
from worktree.core.db.migrations import init_database
from worktree.core.db.repositories.sandboxes import SandboxesRepository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a freshly migrated, isolated SQLite database file."""
    path = tmp_path / "worktree.db"
    init_database(path)
    return path


@pytest.fixture
def db_engine(db_path: Path) -> Engine:
    """Engine bound to the migrated database file, shared across scoped repositories."""
    return get_engine(db_path)


class SandboxesRepositoryTests:
    """Contract tests proving SandboxesRepository queries never cross project_id boundaries."""

    def test_list_scopes_to_repository_project_id(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] SandboxesRepository.list: sandbox created under project_id='proj-a' is absent from a project_id='proj-b' repository's list()."""
        repo_a = SandboxesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = SandboxesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(
            id="sbx_a", branch_name="worktree/sandbox-sbx_a", base_commit="deadbeef", sandbox_path="/tmp/sbx_a"
        )
        repo_b.create(
            id="sbx_b", branch_name="worktree/sandbox-sbx_b", base_commit="deadbeef", sandbox_path="/tmp/sbx_b"
        )

        assert [record.id for record in repo_a.list()] == ["sbx_a"]
        assert [record.id for record in repo_b.list()] == ["sbx_b"]

    def test_get_returns_none_for_sandbox_in_different_project(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] SandboxesRepository.get: sandbox created under project_id='proj-a' returns None when fetched by a project_id='proj-b' repository."""
        repo_a = SandboxesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = SandboxesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(
            id="sbx_a", branch_name="worktree/sandbox-sbx_a", base_commit="deadbeef", sandbox_path="/tmp/sbx_a"
        )

        assert repo_b.get("sbx_a") is None
        assert repo_a.get("sbx_a") is not None
