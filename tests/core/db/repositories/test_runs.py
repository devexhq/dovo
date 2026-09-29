"""Contract tests for RunsRepository project_id scoping."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Engine

from worktree.core.db.connection import get_engine
from worktree.core.db.migrations import init_database
from worktree.core.db.repositories.runs import RunsRepository
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


class RunsRepositoryTests:
    """Contract tests proving RunsRepository queries never cross project_id boundaries."""

    def test_list_scopes_to_repository_project_id(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.list: run created under project_id='proj-a' is absent from a project_id='proj-b' repository's list()."""
        repo_a = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        repo_b.create(session_id="wf_b", blueprint_name="deploy", blueprint_key="deploy")

        assert [record.session_id for record in repo_a.list()] == ["wf_a"]
        assert [record.session_id for record in repo_b.list()] == ["wf_b"]

    def test_get_returns_none_for_run_in_different_project(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.get: run created under project_id='proj-a' returns None when fetched by a project_id='proj-b' repository."""
        repo_a = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert repo_b.get("wf_a") is None
        assert repo_a.get("wf_a") is not None

    def test_create_persists_project_id_on_record(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/integration] RunsRepository.create: returned RunRecord.project_id equals the repository's own project_id."""
        repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")

        record = repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")

        assert record.project_id == "proj-a"


class SandboxRunIndependenceTests:
    """[tier-1/integration] Cross-repository contract: sandbox deletion never mutates a referencing RunRecord."""

    def test_deleting_sandbox_record_does_not_alter_associated_run_record(
        self, db_path: Path, db_engine: Engine, tmp_path: Path
    ) -> None:
        """[tier-1/integration] SandboxesRepository.delete: deleting a sandbox row referenced by RunRecord.sandbox_id leaves RunsRepository.get(session_id) returning the same, unmodified RunRecord."""
        runs_repo = RunsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        sandboxes_repo = SandboxesRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        runs_repo.create(session_id="wf_a", blueprint_name="deploy", blueprint_key="deploy")
        sandboxes_repo.create(
            id="sbx_123", branch_name="feature", base_commit="abc123", sandbox_path=tmp_path / "sbx_123"
        )
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("UPDATE runs SET sandbox_id = ? WHERE session_id = ?", ("sbx_123", "wf_a"))
            connection.commit()
        finally:
            connection.close()
        before = runs_repo.get("wf_a")

        deleted = sandboxes_repo.delete("sbx_123")

        assert deleted is True
        assert runs_repo.get("wf_a") == before
