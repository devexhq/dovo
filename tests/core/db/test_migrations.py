"""Contract tests for the Alembic migration chain."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

import worktree.core.db.migrations as migrations_module
from worktree.core.db.connection import sqlite_url


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
