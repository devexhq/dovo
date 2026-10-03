"""Unified database container for Dovo CLI."""

from pathlib import Path

from sqlalchemy import Engine

from dovo.core.db.connection import get_engine
from dovo.core.db.migrations import init_database
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.db.repositories.costs import CostsRepository
from dovo.core.db.repositories.runs import RunsRepository
from dovo.core.db.repositories.sandboxes import SandboxesRepository


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
        self._sandboxes: SandboxesRepository | None = None
        self._runs: RunsRepository | None = None
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
    def sandboxes(self) -> SandboxesRepository:
        """Repository managing sandbox worktrees and metadata."""
        if self._sandboxes is None:
            self._sandboxes = SandboxesRepository(
                db_path=self.database_file, project_id=self.project_id, auto_init=True, db_engine=self.db_engine
            )
        return self._sandboxes

    @property
    def runs(self) -> RunsRepository:
        """Repository managing blueprint execution runs."""
        if self._runs is None:
            self._runs = RunsRepository(
                db_path=self.database_file, project_id=self.project_id, auto_init=True, db_engine=self.db_engine
            )
        return self._runs

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
        self.sandboxes._initialized = True
        self.runs._initialized = True
        self.costs._initialized = True
        self.artifacts._initialized = True
        return path
