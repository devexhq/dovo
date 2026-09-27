"""Contract tests for the WorktreeDb facade."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktree.core.db.connection import DEFAULT_DB_FILENAME, resolve_db_path
from worktree.core.db.db import WorktreeDb
from worktree.core.db.migrations import init_database
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.db.repositories.costs import CostsRepository
from worktree.core.db.repositories.runs import RunsRepository
from worktree.core.db.repositories.sandboxes import SandboxesRepository
from worktree.core.project.services.identity import generate_project_identity, save_project_identity


def _persist_project_identity(root: Path) -> None:
    """Persist a project identity so WorktreeDb's repositories can resolve project_id."""
    dot_worktree = root / ".worktree"
    dot_worktree.mkdir(parents=True, exist_ok=True)
    save_project_identity(dot_worktree / "project.json", generate_project_identity())


class WorktreeDbTests:
    """Contract tests for WorktreeDb's construction, lazy engine, and repository wiring."""

    def test_init_resolves_path_and_defaults_db_filename(self, tmp_path: Path) -> None:
        """[tier-1/unit] WorktreeDb.__init__: path and cwd resolve to the absolute directory; db_filename defaults to DEFAULT_DB_FILENAME."""
        db = WorktreeDb(tmp_path)

        assert db.path == tmp_path.resolve()
        assert db.cwd == tmp_path.resolve()
        assert db.db_filename == DEFAULT_DB_FILENAME

    def test_init_accepts_custom_db_filename(self, tmp_path: Path) -> None:
        """[tier-1/unit] WorktreeDb.__init__: an explicit db_filename overrides the default."""
        db = WorktreeDb(tmp_path, db_filename="custom.db")

        assert db.db_filename == "custom.db"

    def test_db_engine_binds_to_resolved_db_path_and_caches(self, tmp_path: Path) -> None:
        """[tier-1/unit] WorktreeDb.db_engine: lazily binds to resolve_db_path(db_filename) and returns the same instance on repeat access."""
        db = WorktreeDb(tmp_path, db_filename="custom.db")

        engine = db.db_engine

        assert Path(engine.url.database or "") == resolve_db_path("custom.db")
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
        """[tier-1/unit] WorktreeDb repository properties: return the correct repository type scoped to the facade's path/db_filename/db_engine, and are cached across repeat access."""
        db = WorktreeDb(tmp_path, db_filename="custom.db")

        repo = getattr(db, attribute)

        assert isinstance(repo, repo_type)
        assert repo.path == db.path
        assert repo.db_filename == db.db_filename
        assert repo.db_engine is db.db_engine
        assert getattr(db, attribute) is repo

    def test_init_db_migrates_and_returns_existing_file_path(self, tmp_path: Path) -> None:
        """[tier-1/integration] WorktreeDb.init_db: runs migrations against the resolved database path and returns a path that exists on disk."""
        db = WorktreeDb(tmp_path, db_filename="custom.db")

        migrated_path = db.init_db()

        assert migrated_path == resolve_db_path("custom.db")
        assert migrated_path.is_file()

    def test_init_db_marks_repositories_initialized_so_first_query_skips_remigration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] WorktreeDb.init_db: migrates once and marks all three repositories initialized, so the first subsequent repository query does not re-run migrations."""
        _persist_project_identity(tmp_path)
        db = WorktreeDb(tmp_path)
        call_count = 0
        original_init_database = init_database

        def _counting_init_database(db_filename: str = DEFAULT_DB_FILENAME, db_path: Path | None = None) -> Path:
            nonlocal call_count
            call_count += 1
            return original_init_database(db_filename=db_filename, db_path=db_path)

        monkeypatch.setattr("worktree.core.db.db.init_database", _counting_init_database)
        monkeypatch.setattr("worktree.core.db.repositories.base.init_database", _counting_init_database)

        db.init_db()
        assert call_count == 1

        record = db.runs.create(session_id="wf_abc123", blueprint_name="deploy", blueprint_key="deploy")

        assert record.session_id == "wf_abc123"
        assert call_count == 1
