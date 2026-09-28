"""SQLite connection, engine factory, and path resolution helpers."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

from worktree.common.filesystem.models import GlobalPaths

DEFAULT_DB_FILENAME = "worktree.db"


def resolve_db_path(global_paths: GlobalPaths, db_filename: str = DEFAULT_DB_FILENAME) -> Path:
    """Resolve the centralized database file path under the global Worktree data directory."""
    return global_paths.data_dir / db_filename


def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Enable WAL mode and busy timeout for safe concurrent CLI + Desktop access."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            dbapi_connection.close()
        except Exception:
            pass
        raise
    cursor.close()


def sqlite_url(path: Path) -> str:
    """Format an SQLite database file path as a SQLAlchemy connection URL."""
    return f"sqlite:///{path}"


def get_engine(db_path: Path) -> Engine:
    """Create a SQLModel/SQLAlchemy engine configured with SQLite WAL pragmas."""
    engine = create_engine(
        sqlite_url(db_path),
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


@contextmanager
def get_session(engine: Engine) -> Generator[Session]:
    """Context manager offering clean database session setup and safe teardown."""
    with Session(engine) as session:
        yield session


@contextmanager
def get_db_connection(db_path: Path) -> Generator[sqlite3.Connection]:
    """Context manager offering clean database connection setup and safe teardown."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
