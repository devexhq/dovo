"""Database probe for workspace status."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SessionsRepository
from dovo.core.status.models import DatabaseStatusInfo


def probe_database(paths: WorkspacePaths) -> DatabaseStatusInfo:
    """Return whether the project database exists and answers a session query, and its session count, without migrating it."""
    db_path = paths.database_file
    if not db_path.is_file():
        return DatabaseStatusInfo(
            exists=False,
            db_path=db_path,
            is_accessible=False,
            total_sessions=0,
        )

    try:
        sessions_repo = SessionsRepository(db_path=db_path, project_id=paths.project_id, auto_init=False)
        total_sessions = len(sessions_repo.list())
        return DatabaseStatusInfo(
            exists=True,
            db_path=db_path,
            is_accessible=True,
            total_sessions=total_sessions,
        )
    except Exception:
        return DatabaseStatusInfo(
            exists=True,
            db_path=db_path,
            is_accessible=False,
            total_sessions=0,
        )
