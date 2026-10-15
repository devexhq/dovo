"""Contract tests for ArtifactsRepository."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.models import ArtifactRecord
from dovo.core.db.repositories.artifacts import ArtifactsRepository


def _create(
    repo: ArtifactsRepository,
    session_id: str,
    name: str,
    path: str,
    *,
    revision: int = 1,
    expires_at: str | None = None,
    digest: str | None = None,
) -> ArtifactRecord:
    """Insert one artifact revision row with unremarkable size and no provenance."""
    return repo.create(
        session_id,
        name,
        path,
        revision=revision,
        size_bytes=1,
        file_count=1,
        expires_at=expires_at,
        digest=digest,
        step_id=None,
        attempt=None,
        loop_iteration=None,
    )


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

    def test_create_duplicate_revision_raises_value_error(self, db_path: Path, db_engine: Engine) -> None:
        repo = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        _create(repo, "wf_1", "dist", "/tmp/artifacts/wf_1/dist/1", digest="first")

        with pytest.raises(ValueError):
            _create(repo, "wf_1", "dist", "/tmp/artifacts/wf_1/dist/other", digest="second")

        stored = repo.get("wf_1", "dist")
        assert stored is not None
        assert (stored.path, stored.digest) == (Path("/tmp/artifacts/wf_1/dist/1"), "first")

    def test_next_revision_and_get_resolve_latest(self, db_path: Path, db_engine: Engine) -> None:
        repo = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        assert repo.next_revision("wf_1", "dist") == 1
        _create(repo, "wf_1", "dist", "/tmp/dist/1", revision=1)
        _create(repo, "wf_1", "dist", "/tmp/dist/2", revision=2)

        latest = repo.get("wf_1", "dist")
        first = repo.get("wf_1", "dist", revision=1)

        assert repo.next_revision("wf_1", "dist") == 3
        assert latest is not None and latest.revision == 2
        assert first is not None and first.revision == 1
        assert repo.get("wf_1", "dist", revision=9) is None

    def test_list_returns_one_row_per_revision_newest_first(self, db_path: Path, db_engine: Engine) -> None:
        repo = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        _create(repo, "wf_1", "dist", "/tmp/dist/1", revision=1)
        _create(repo, "wf_1", "dist", "/tmp/dist/2", revision=2)

        assert [record.revision for record in repo.list(session_id="wf_1")] == [2, 1]
        assert repo.delete("wf_1", "dist", 1) is True
        assert [record.revision for record in repo.list(session_id="wf_1")] == [2]
        assert repo.delete("wf_1", "dist", 1) is False

    def test_list_filters_by_session_and_scopes_to_project_id(self, db_path: Path, db_engine: Engine) -> None:
        repo_a = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        _create(repo_a, "wf_1", "dist", "/tmp/a/dist")
        _create(repo_a, "wf_2", "coverage", "/tmp/a/coverage")
        _create(repo_b, "wf_1", "dist", "/tmp/b/dist")

        assert [record.name for record in repo_a.list(session_id="wf_1")] == ["dist"]
        assert {record.session_id for record in repo_b.list()} == {"wf_1"}

    def test_list_expired_excludes_null_and_future_expires_at(self, db_path: Path, db_engine: Engine) -> None:
        repo = ArtifactsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        _create(repo, "wf_1", "never-expires", "/tmp/a")
        _create(repo, "wf_1", "future", "/tmp/b", expires_at="2999-01-01 00:00:00")
        _create(repo, "wf_1", "expired", "/tmp/c", expires_at="2000-01-01 00:00:00")

        expired = repo.list_expired("2025-01-01 00:00:00")

        assert [record.name for record in expired] == ["expired"]
