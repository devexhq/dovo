"""Database migration routines using Alembic programmatic API."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.lock import WorkspaceLock
from worktree.core.db.connection import (
    DEFAULT_DB_FILENAME,
    resolve_db_path,
    sqlite_url,
)

INITIAL_SCHEMA_REVISION = "0001_initial_schema"
LATEST_SCHEMA_REVISION = "0003_add_artifacts_table"


def init_database(
    db_filename: str = DEFAULT_DB_FILENAME,
    db_path: Path | None = None,
) -> Path:
    """Run table migrations and initialize the centralized global SQLite database layout."""
    target_path = db_path if db_path is not None else resolve_db_path(db_filename)

    with WorkspaceLock(resolve_global_paths().data_dir):
        target_path.parent.mkdir(parents=True, exist_ok=True)

        alembic_cfg = Config()
        alembic_dir = Path(__file__).parent / "alembic"
        alembic_cfg.set_main_option("script_location", str(alembic_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", sqlite_url(target_path))

        command.upgrade(alembic_cfg, "head")

        return target_path
