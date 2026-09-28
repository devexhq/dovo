"""Base repository implementation managing DB initialization and session factories."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel
from sqlmodel.sql.expression import Select, SelectOfScalar

from worktree.core.db.connection import get_engine, get_session
from worktree.core.db.migrations import init_database

RecordT = TypeVar("RecordT", bound=SQLModel)


class BaseRepository:
    """Base repository providing SQLModel session lifecycle and lazy database initialization."""

    def __init__(
        self,
        path: Path | None = None,
        db_path: Path | None = None,
        auto_init: bool = True,
        db_engine: Engine | None = None,
        project_id: str | None = None,
    ) -> None:
        self.path = path
        self.cwd = path
        self._db_path = db_path
        self._auto_init = auto_init
        self._initialized = False
        self._db_engine: Engine | None = db_engine
        self._project_id = project_id

    @property
    def db_path(self) -> Path:
        """Explicitly supplied database file path."""
        if self._db_path is None:
            raise ValueError("db_path must be provided")
        return self._db_path

    @property
    def project_id(self) -> str:
        """Explicitly supplied project identifier scoping this repository's queries."""
        if self._project_id is None:
            raise ValueError("project_id must be provided")
        return self._project_id

    @property
    def db_engine(self) -> Engine:
        """SQLAlchemy / SQLModel Engine bound to db_path."""
        if self._db_engine is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_engine = get_engine(self.db_path)
        return self._db_engine

    @property
    def engine(self) -> Engine:
        """Alias to db_engine."""
        return self.db_engine

    @contextmanager
    def session(self) -> Generator[Session]:
        """Create and yield a new SQLModel session, running migrations lazily if auto_init is enabled."""
        if self._auto_init:
            self._ensure_initialized()
        with get_session(self.db_engine) as sess:
            yield sess

    def _commit(self, session: Session, record: RecordT, conflict_message: str | None = None) -> RecordT:
        """Add record, commit transaction, rollback and raise ValueError on IntegrityError, and refresh record."""
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError(conflict_message or "Database operation failed") from exc
        session.refresh(record)
        return record

    def _delete_where(
        self,
        session: Session,
        statement: Select[tuple[RecordT]] | SelectOfScalar[RecordT],
    ) -> bool:
        """Execute statement, delete the first matching record if found, commit, and return True if deleted."""
        record = session.exec(statement).first()
        if record is None:
            return False
        session.delete(record)
        session.commit()
        return True

    def init_db(self) -> Path:
        """Explicitly run table migrations and mark repository initialized."""
        path = init_database(self.db_path)
        self._initialized = True
        return path

    def _ensure_initialized(self) -> None:
        """Trigger database schema creation if not already executed on this repository instance."""
        if not self._initialized:
            self.init_db()
