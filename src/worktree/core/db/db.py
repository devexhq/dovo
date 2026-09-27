"""Unified database container for Worktree CLI."""

from pathlib import Path

from sqlalchemy import Engine

from worktree.core.db.connection import (
    DEFAULT_DB_FILENAME,
    get_engine,
    resolve_db_path,
)
from worktree.core.db.migrations import init_database
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.db.repositories.costs import CostsRepository
from worktree.core.db.repositories.runs import RunsRepository
from worktree.core.db.repositories.sandboxes import SandboxesRepository


class WorktreeDb:
    """Unified entry point providing access to all domain DB repositories under a single configuration."""

    def __init__(
        self,
        path: Path,
        db_filename: str = DEFAULT_DB_FILENAME,
        db_engine: Engine | None = None,
    ) -> None:
        self.path = path.resolve()
        self.cwd = self.path
        self.db_filename = db_filename
        self._db_engine = db_engine
        self._sandboxes: SandboxesRepository | None = None
        self._runs: RunsRepository | None = None
        self._costs: CostsRepository | None = None
        self._artifacts: ArtifactsRepository | None = None

    @property
    def db_engine(self) -> Engine:
        """SQLAlchemy / SQLModel Engine bound to resolved database path."""
        if self._db_engine is None:
            self._db_engine = get_engine(resolve_db_path(self.db_filename))
        return self._db_engine

    @property
    def sandboxes(self) -> SandboxesRepository:
        """Repository managing sandbox worktrees and metadata."""
        if self._sandboxes is None:
            self._sandboxes = SandboxesRepository(
                self.path, db_filename=self.db_filename, auto_init=True, db_engine=self.db_engine
            )
        return self._sandboxes

    @property
    def runs(self) -> RunsRepository:
        """Repository managing blueprint execution runs."""
        if self._runs is None:
            self._runs = RunsRepository(
                self.path, db_filename=self.db_filename, auto_init=True, db_engine=self.db_engine
            )
        return self._runs

    @property
    def costs(self) -> CostsRepository:
        """Repository managing tracked token costs."""
        if self._costs is None:
            self._costs = CostsRepository(
                self.path, db_filename=self.db_filename, auto_init=True, db_engine=self.db_engine
            )
        return self._costs

    @property
    def artifacts(self) -> ArtifactsRepository:
        """Repository managing published session artifact metadata."""
        if self._artifacts is None:
            self._artifacts = ArtifactsRepository(
                self.path, db_filename=self.db_filename, auto_init=True, db_engine=self.db_engine
            )
        return self._artifacts

    def init_db(self) -> Path:
        """Run migrations and mark all child repositories as initialized."""
        path = init_database(db_filename=self.db_filename)
        self.sandboxes._initialized = True
        self.runs._initialized = True
        self.costs._initialized = True
        self.artifacts._initialized = True
        return path
