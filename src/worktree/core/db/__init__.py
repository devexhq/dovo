"""src/worktree/core/db package.

Handles SQLite connection management, database migrations, financial token
usage tracking, catalog indexing, and unified run execution tracking.
"""

from worktree.core.db.connection import (
    DEFAULT_DB_FILENAME,
    get_db_connection,
    get_engine,
    get_session,
    resolve_db_path,
    sqlite_url,
)
from worktree.core.db.db import WorktreeDb
from worktree.core.db.migrations import (
    INITIAL_SCHEMA_REVISION,
    LATEST_SCHEMA_REVISION,
    init_database,
)
from worktree.core.db.models import (
    ArtifactRecord,
    CostRecord,
    RunRecord,
    RunStatus,
    SandboxRecord,
    SandboxStatus,
    parse_timestamp,
)
from worktree.core.db.repositories import (
    ArtifactsRepository,
    BaseRepository,
    CostsRepository,
    RunsRepository,
    SandboxesRepository,
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
    "RunRecord",
    "RunStatus",
    "RunsRepository",
    "SandboxRecord",
    "SandboxStatus",
    "SandboxesRepository",
    "WorktreeDb",
    "get_db_connection",
    "get_engine",
    "get_session",
    "init_database",
    "parse_timestamp",
    "resolve_db_path",
    "sqlite_url",
]
