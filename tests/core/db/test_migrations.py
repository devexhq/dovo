"""Contract tests for the Alembic migration chain."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dovo.core.db.migrations import init_database


class InitDatabaseTests:
    """[tier-1/integration] init_database: caller-supplied database_file handling."""

    def test_database_file_creates_tables_at_that_exact_location(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(database_file): schema is created at the caller-supplied path, and that exact path is returned."""
        explicit_path = tmp_path / "custom" / "nested" / "dovo.db"

        returned_path = init_database(explicit_path)

        assert returned_path == explicit_path
        assert explicit_path.is_file()
        connection = sqlite3.connect(explicit_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert "runs" in tables

    def test_database_file_with_missing_parent_directories_creates_them(self, tmp_path: Path) -> None:
        """[tier-1/integration] init_database(database_file): parent directories that do not yet exist are created before the database file is written."""
        explicit_path = tmp_path / "does" / "not" / "exist" / "yet" / "dovo.db"
        assert not explicit_path.parent.exists()

        init_database(explicit_path)

        assert explicit_path.is_file()
