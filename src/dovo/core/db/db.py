"""Unified database container for Dovo CLI."""

from pathlib import Path

from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.db.repositories.costs import CostsRepository
from dovo.core.db.repositories.sessions import SessionsRepository
from dovo.core.db.repositories.worktrees import WorktreesRepository


class DovoDb:
    """Unified entry point providing access to all domain DB repositories under a single configuration."""

    def __init__(
        self,
        database_file: Path,
        project_id: str | None = None,
        db_engine: Engine | None = None,
    ) -> None:
        """Bind this DovoDb to a resolved database file and optional project scope."""
        self.database_file = database_file
        self.project_id = project_id
        self._db_engine = db_engine
        self._worktrees: WorktreesRepository | None = None
        self._sessions: SessionsRepository | None = None
        self._costs: CostsRepository | None = None
        self._artifacts: ArtifactsRepository | None = None

    @property
    def db_engine(self) -> Engine:
        """SQLAlchemy / SQLModel Engine bound to database_file."""
        if self._db_engine is None:
            self.database_file.parent.mkdir(parents=True, exist_ok=True)
            self._db_engine = get_engine(self.database_file)
        return self._db_engine

    @property
    def worktrees(self) -> WorktreesRepository:
        """Repository managing worktrees and metadata."""
        if self._worktrees is None:
            self._worktrees = WorktreesRepository(
                db_path=self.database_file, project_id=self.project_id, auto_init=True, db_engine=self.db_engine
            )
        return self._worktrees

    @property
    def sessions(self) -> SessionsRepository:
        """Repository managing session records."""
        if self._sessions is None:
            self._sessions = SessionsRepository(
                db_path=self.database_file, project_id=self.project_id, auto_init=True, db_engine=self.db_engine
            )
        return self._sessions

    @property
    def costs(self) -> CostsRepository:
        """Repository managing tracked token costs."""
        if self._costs is None:
            self._costs = CostsRepository(
                db_path=self.database_file, project_id=self.project_id, auto_init=True, db_engine=self.db_engine
            )
        return self._costs

    @property
    def artifacts(self) -> ArtifactsRepository:
        """Repository managing published session artifact metadata."""
        if self._artifacts is None:
            self._artifacts = ArtifactsRepository(
                db_path=self.database_file, project_id=self.project_id, auto_init=True, db_engine=self.db_engine
            )
        return self._artifacts

    def init_db(self) -> Path:
        """Run migrations and mark all child repositories as initialized."""
        path = init_database(self.database_file)
        self.worktrees._initialized = True
        self.sessions._initialized = True
        self.costs._initialized = True
        self.artifacts._initialized = True
        return path
