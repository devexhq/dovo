"""Contract tests for ArtifactsRepository."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.repositories.artifacts import ArtifactsRepository


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


class ArtifactsRepositoryTests:
    """Contract tests for ArtifactsRepository CRUD and project scoping."""

    def test_create_duplicate_session_and_name_overwrites_existing_row(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/unit] ArtifactsRepository.create: a second create() call for the same (session_id, name) updates path/size_bytes/file_count/expires_at on the existing row instead of raising or duplicating it."""
        repo = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create("wf_1", "dist", "/tmp/artifacts/wf_1/dist", size_bytes=10, file_count=1, expires_at=None)

        updated = repo.create(
            "wf_1", "dist", "/tmp/artifacts/wf_1/dist-v2", size_bytes=20, file_count=2, expires_at="2030-01-01"
        )

        rows = repo.list(session_id="wf_1")
        assert len(rows) == 1
        assert updated.path == Path("/tmp/artifacts/wf_1/dist-v2")
        assert updated.size_bytes == 20
        assert updated.file_count == 2
        assert updated.expires_at == "2030-01-01"

    def test_list_filters_by_session_and_scopes_to_project_id(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/unit] ArtifactsRepository.list: passing session_id returns only that session's rows; rows from a different project_id are never returned."""
        repo_a = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.create("wf_1", "dist", "/tmp/a/dist", size_bytes=1, file_count=1, expires_at=None)
        repo_a.create("wf_2", "coverage", "/tmp/a/coverage", size_bytes=1, file_count=1, expires_at=None)
        repo_b.create("wf_1", "dist", "/tmp/b/dist", size_bytes=1, file_count=1, expires_at=None)

        assert [record.name for record in repo_a.list(session_id="wf_1")] == ["dist"]
        assert {record.session_id for record in repo_b.list()} == {"wf_1"}

    def test_list_expired_excludes_null_and_future_expires_at(self, db_path: Path, db_engine: Engine) -> None:
        """[tier-1/unit] ArtifactsRepository.list_expired: a row with expires_at=None and a row with a future expires_at are both excluded; only past-expires_at rows are returned."""
        repo = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo.create("wf_1", "never-expires", "/tmp/a", size_bytes=1, file_count=1, expires_at=None)
        repo.create("wf_1", "future", "/tmp/b", size_bytes=1, file_count=1, expires_at="2999-01-01 00:00:00")
        repo.create("wf_1", "expired", "/tmp/c", size_bytes=1, file_count=1, expires_at="2000-01-01 00:00:00")

        expired = repo.list_expired("2025-01-01 00:00:00")

        assert [record.name for record in expired] == ["expired"]
