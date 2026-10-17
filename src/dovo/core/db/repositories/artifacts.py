"""Repository managing artifact metadata CRUD operations using SQLModel."""

from __future__ import annotations

import builtins
from pathlib import Path

from sqlmodel import col, func, select

from dovo.core.db.models import ArtifactRecord
from dovo.core.db.repositories.base import BaseRepository


class ArtifactsRepository(BaseRepository):
    """Repository managing artifact metadata CRUD operations using SQLModel."""

    def create(
        self,
        session_id: str,
        name: str,
        path: Path | str,
        *,
        revision: int,
        size_bytes: int,
        file_count: int,
        expires_at: str | None,
        digest: str | None,
        step_id: str | None,
        attempt: int | None,
        loop_iteration: int | None,
    ) -> ArtifactRecord:
        """Insert one artifact revision row; raise ValueError when (session_id, name, revision) already exists."""
        with self.session() as session:
            record = ArtifactRecord(
                project_id=self.project_id,
                session_id=session_id,
                name=name,
                path=Path(str(path)),
                revision=revision,
                digest=digest,
                step_id=step_id,
                attempt=attempt,
                loop_iteration=loop_iteration,
                size_bytes=size_bytes,
                file_count=file_count,
                expires_at=expires_at,
            )
            return self._commit(session, record)

    def next_revision(self, session_id: str, name: str) -> int:
        """Return max(existing revision) + 1 for (session_id, name), or 1 when none exists."""
        with self.session() as session:
            statement = select(func.max(ArtifactRecord.revision)).where(
                ArtifactRecord.project_id == self.project_id,
                ArtifactRecord.session_id == session_id,
                ArtifactRecord.name == name,
            )
            latest = session.exec(statement).one()
            return (latest or 0) + 1

    def get(self, session_id: str, name: str, revision: int | None = None) -> ArtifactRecord | None:
        """Return the row for (session_id, name, revision), or the highest revision when revision is None."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(
                ArtifactRecord.project_id == self.project_id,
                ArtifactRecord.session_id == session_id,
                ArtifactRecord.name == name,
            )
            if revision is not None:
                statement = statement.where(ArtifactRecord.revision == revision)
            return session.exec(statement.order_by(col(ArtifactRecord.revision).desc())).first()

    def list(self, session_id: str | None = None) -> builtins.list[ArtifactRecord]:
        """List artifact revision rows for the current project, optionally filtered to one session, newest first."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(ArtifactRecord.project_id == self.project_id)
            if session_id is not None:
                statement = statement.where(ArtifactRecord.session_id == session_id)
            statement = statement.order_by(col(ArtifactRecord.created_at).desc(), col(ArtifactRecord.revision).desc())
            return list(session.exec(statement).all())

    def delete(self, session_id: str, name: str, revision: int) -> bool:
        """Hard-delete one artifact revision row. Returns whether a row was removed."""
        with self.session() as session:
            return self._delete_where(
                session,
                select(ArtifactRecord).where(
                    ArtifactRecord.project_id == self.project_id,
                    ArtifactRecord.session_id == session_id,
                    ArtifactRecord.name == name,
                    ArtifactRecord.revision == revision,
                ),
            )

    def list_expired(self, now: str) -> builtins.list[ArtifactRecord]:
        """List artifact rows whose expires_at is set and at or before now, for the current project."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(
                ArtifactRecord.project_id == self.project_id,
                col(ArtifactRecord.expires_at).is_not(None),
                col(ArtifactRecord.expires_at) <= now,
            )
            return list(session.exec(statement).all())
