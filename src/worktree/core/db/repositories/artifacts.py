"""Repository managing artifact metadata CRUD operations using SQLModel."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import col, select

from worktree.core.db.models import ArtifactRecord
from worktree.core.db.repositories.base import BaseRepository


class ArtifactsRepository(BaseRepository):
    """Repository managing artifact metadata CRUD operations using SQLModel."""

    def create(
        self,
        session_id: str,
        name: str,
        path: Path | str,
        *,
        size_bytes: int,
        file_count: int,
        expires_at: str | None,
    ) -> ArtifactRecord:
        """Insert an artifact row, or overwrite the existing row for this (session_id, name) pair."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(
                ArtifactRecord.project_id == self.project_id,
                ArtifactRecord.session_id == session_id,
                ArtifactRecord.name == name,
            )
            existing = session.exec(statement).first()

            if existing is not None:
                existing.path = Path(str(path))
                existing.size_bytes = size_bytes
                existing.file_count = file_count
                existing.expires_at = expires_at
                return self._commit(session, existing)

            record = ArtifactRecord(
                project_id=self.project_id,
                session_id=session_id,
                name=name,
                path=Path(str(path)),
                size_bytes=size_bytes,
                file_count=file_count,
                expires_at=expires_at,
            )
            return self._commit(session, record)

    def get(self, session_id: str, name: str) -> ArtifactRecord | None:
        """Return the artifact row for (session_id, name), or None when missing."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(
                ArtifactRecord.project_id == self.project_id,
                ArtifactRecord.session_id == session_id,
                ArtifactRecord.name == name,
            )
            return session.exec(statement).first()

    def list(self, session_id: str | None = None) -> list[ArtifactRecord]:
        """List artifact rows for the current project, optionally filtered to one session, newest first."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(ArtifactRecord.project_id == self.project_id)
            if session_id is not None:
                statement = statement.where(ArtifactRecord.session_id == session_id)
            statement = statement.order_by(col(ArtifactRecord.created_at).desc())
            return list(session.exec(statement).all())

    def delete(self, session_id: str, name: str) -> bool:
        """Hard-delete an artifact metadata row. Returns whether a row was removed."""
        with self.session() as session:
            return self._delete_where(
                session,
                select(ArtifactRecord).where(
                    ArtifactRecord.project_id == self.project_id,
                    ArtifactRecord.session_id == session_id,
                    ArtifactRecord.name == name,
                ),
            )

    def list_expired(self, now: str) -> list[ArtifactRecord]:
        """List artifact rows whose expires_at is set and at or before now, for the current project."""
        with self.session() as session:
            statement = select(ArtifactRecord).where(
                ArtifactRecord.project_id == self.project_id,
                col(ArtifactRecord.expires_at).is_not(None),
                col(ArtifactRecord.expires_at) <= now,
            )
            return list(session.exec(statement).all())
