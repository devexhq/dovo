"""src/dovo/core/db package.

Handles SQLite connection management, database migrations, financial token
usage tracking, catalog indexing, and unified session tracking.
"""

from dovo.core.db.connection import (
    DEFAULT_DB_FILENAME,
    get_db_connection,
    get_engine,
    get_session,
    resolve_db_path,
    sqlite_url,
)
from dovo.core.db.db import DovoDb
from dovo.core.db.migrations import (
    INITIAL_SCHEMA_REVISION,
    LATEST_SCHEMA_REVISION,
    init_database,
)
from dovo.core.db.models import (
    ArtifactRecord,
    CostRecord,
    SessionRecord,
    SessionStatus,
    WorktreeRecord,
    WorktreeStatus,
    parse_timestamp,
)
from dovo.core.db.repositories import (
    ArtifactsRepository,
    BaseRepository,
    CostsRepository,
    SessionsRepository,
    WorktreesRepository,
)

__all__ = [
    "DEFAULT_DB_FILENAME",
    "INITIAL_SCHEMA_REVISION",
    "LATEST_SCHEMA_REVISION",
    "ArtifactRecord",
    "ArtifactsRepository",
    "BaseRepository",
    "CostRecord",
    "CostsRepository",
    "DovoDb",
    "SessionRecord",
    "SessionStatus",
    "SessionsRepository",
    "WorktreeRecord",
    "WorktreeStatus",
    "WorktreesRepository",
    "get_db_connection",
    "get_engine",
    "get_session",
    "init_database",
    "parse_timestamp",
    "resolve_db_path",
    "sqlite_url",
]
