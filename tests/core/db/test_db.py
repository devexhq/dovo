"""Contract tests for the WorktreeDb facade."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree.core.db.db import WorktreeDb
from worktree.core.db.migrations import init_database
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.db.repositories.costs import CostsRepository
from worktree.core.db.repositories.runs import RunsRepository
from worktree.core.db.repositories.sandboxes import SandboxesRepository


class WorktreeDbTests:
    """Contract tests for WorktreeDb's construction, lazy engine, and repository wiring."""

    def test_init_stores_database_file_and_project_id(self, tmp_path: Path) -> None:
        """[tier-1/unit] WorktreeDb.__init__: database_file and project_id are stored as given."""
        database_file = tmp_path / "worktree.db"

        db = WorktreeDb(database_file, project_id="proj-1")

        assert db.database_file == database_file
        assert db.project_id == "proj-1"

    def test_db_engine_binds_to_database_file_and_caches(self, tmp_path: Path) -> None:
        """[tier-1/unit] WorktreeDb.db_engine: lazily binds to database_file and returns the same instance on repeat access, creating the parent directory."""
        database_file = tmp_path / "nested" / "worktree.db"
        db = WorktreeDb(database_file)

        engine = db.db_engine

        assert Path(engine.url.database or "") == database_file
        assert database_file.parent.is_dir()
        assert db.db_engine is engine

    @pytest.mark.parametrize(
        ("attribute", "repo_type"),
        [
            pytest.param("sandboxes", SandboxesRepository, id="sandboxes"),
            pytest.param("runs", RunsRepository, id="runs"),
            pytest.param("costs", CostsRepository, id="costs"),
            pytest.param("artifacts", ArtifactsRepository, id="artifacts"),
        ],
    )
    def test_repository_properties_share_facade_state_and_cache(
        self, tmp_path: Path, attribute: str, repo_type: type
    ) -> None:
        """[tier-1/unit] WorktreeDb repository properties: return the correct repository type scoped to the facade's database_file/project_id/db_engine, and are cached across repeat access."""
        db = WorktreeDb(tmp_path / "worktree.db", project_id="proj-1")

        repo = getattr(db, attribute)

        assert isinstance(repo, repo_type)
        assert repo.db_path == db.database_file
        assert repo.project_id == db.project_id
        assert repo.db_engine is db.db_engine
        assert getattr(db, attribute) is repo

    def test_init_db_migrates_and_returns_existing_file_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] WorktreeDb.init_db: runs migrations against database_file and returns a path that exists on disk."""
        database_file = tmp_path / "worktree.db"
        db = WorktreeDb(database_file)

        migrated_path = db.init_db()

        assert migrated_path == database_file
        assert migrated_path.is_file()

    def test_init_db_marks_repositories_initialized_so_first_query_skips_remigration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] WorktreeDb.init_db: migrates once and marks all four repositories initialized, so the first subsequent repository query does not re-run migrations."""
        db = WorktreeDb(tmp_path / "worktree.db", project_id="proj-1")
        call_count = 0
        original_init_database = init_database

        def _counting_init_database(database_file: Path) -> Path:
            nonlocal call_count
            call_count += 1
            return original_init_database(database_file)

        monkeypatch.setattr("worktree.core.db.db.init_database", _counting_init_database)
        monkeypatch.setattr("worktree.core.db.repositories.base.init_database", _counting_init_database)

        db.init_db()
        assert call_count == 1

        record = db.runs.create(session_id="wf_abc123", blueprint_name="deploy", blueprint_key="deploy")

        assert record.session_id == "wf_abc123"
        assert call_count == 1
