"""Contract tests for the Alembic migration chain."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

import worktree.core.db.migrations as migrations_module
from worktree.core.db.connection import sqlite_url
from worktree.core.db.migrations import init_database


def _alembic_config(db_path: Path) -> Config:
    """Build an Alembic Config bound to an isolated SQLite database file."""
    alembic_cfg = Config()
    alembic_dir = Path(migrations_module.__file__).parent / "alembic"
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    return alembic_cfg


class DropCatalogTableMigrationTests:
    """[tier-1/integration] Migration-chain contracts for 0002_drop_catalog_table."""

    def test_upgrade_to_0002_drops_catalog_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "0001_initial_schema")
        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()

        assert "catalog" not in tables


class AddArtifactsTableMigrationTests:
    """[tier-1/integration] Migration-chain contracts for 0003_add_artifacts_table."""

    def test_upgrade_to_0003_creates_artifacts_table_with_unique_constraint(self, tmp_path: Path) -> None:
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "head")

        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            unique_indexes = [row for row in connection.execute("PRAGMA index_list(artifacts)") if row[2] == 1]
        finally:
            connection.close()

        assert "artifacts" in tables
        assert len(unique_indexes) == 1

    def test_downgrade_from_0003_drops_artifacts_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "worktree.db"
        alembic_cfg = _alembic_config(db_path)

        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "0002_drop_catalog_table")

        connection = sqlite3.connect(db_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()

        assert "artifacts" not in tables


class InitDatabaseTests:
    """[tier-1/integration] init_database: direct db_path handling and default resolution."""

    def test_explicit_db_path_creates_tables_at_that_exact_location(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(db_path=...): schema is created at the caller-supplied path, not the default global data directory, and that exact path is returned."""
        explicit_path = tmp_path / "custom" / "nested" / "worktree.db"

        returned_path = init_database(db_path=explicit_path)

        assert returned_path == explicit_path
        assert explicit_path.is_file()
        connection = sqlite3.connect(explicit_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert "runs" in tables

    def test_explicit_db_path_with_missing_parent_directories_creates_them(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(db_path=...): parent directories that do not yet exist are created before the database file is written."""
        explicit_path = tmp_path / "does" / "not" / "exist" / "yet" / "worktree.db"
        assert not explicit_path.parent.exists()

        init_database(db_path=explicit_path)

        assert explicit_path.is_file()

    def test_no_db_path_resolves_default_location_under_worktree_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] init_database(): db_path omitted -> resolves via resolve_db_path() under WORKTREE_HOME's data directory, and the returned path exists on disk."""
        global_root = tmp_path / "global-home"
        monkeypatch.setenv("WORKTREE_HOME", str(global_root))

        returned_path = init_database()

        assert returned_path == global_root / "data" / "worktree.db"
        assert returned_path.is_file()
