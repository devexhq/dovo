"""Database migration routines using Alembic programmatic API."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from dovo.common.lock import WorkspaceLock, resolve_lock_file_path
from dovo.core.db.connection import sqlite_url

INITIAL_SCHEMA_REVISION = "0001_initial_schema"
LATEST_SCHEMA_REVISION = INITIAL_SCHEMA_REVISION


def init_database(database_file: Path) -> Path:
    """Run table migrations and initialize the centralized global SQLite database layout."""
    with WorkspaceLock(resolve_lock_file_path(database_file.parent)):
        database_file.parent.mkdir(parents=True, exist_ok=True)

        alembic_cfg = Config()
        alembic_dir = Path(__file__).parent / "alembic"
        alembic_cfg.set_main_option("script_location", str(alembic_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", sqlite_url(database_file))

        command.upgrade(alembic_cfg, "head")

        return database_file
