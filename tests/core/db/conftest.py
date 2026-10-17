"""Shared database fixtures for dovo.core.db tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database


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
