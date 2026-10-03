"""Repository managing sandbox metadata CRUD operations using SQLModel."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import col, select

from dovo.core.db.models import SandboxRecord, SandboxStatus
from dovo.core.db.repositories.base import BaseRepository


class SandboxesRepository(BaseRepository):
    """Repository managing sandbox metadata CRUD operations using SQLModel."""

    def create(
        self,
        id: str,
        branch_name: str,
        base_commit: str,
        sandbox_path: Path | str,
        name: str | None = None,
    ) -> SandboxRecord:
        """Create a sandbox metadata row with status ``active``.

        Returns:
            The created `SandboxRecord`, including DB-assigned timestamps.

        Raises:
            ValueError: If a row with the same ``id`` already exists.
        """
        record = SandboxRecord(
            id=id,
            project_id=self.project_id,
            name=name,
            branch_name=branch_name,
            base_commit=base_commit,
            sandbox_path=Path(str(sandbox_path)),
            status=SandboxStatus.ACTIVE,
        )

        with self.session() as session:
            return self._commit(session, record, f"Sandbox with id '{id}' already exists")

    def get(self, id: str) -> SandboxRecord | None:
        """Return the sandbox row for ``id``, or ``None`` when missing."""
        with self.session() as session:
            statement = select(SandboxRecord).where(SandboxRecord.id == id, SandboxRecord.project_id == self.project_id)
            return session.exec(statement).first()

    def list(self, status: SandboxStatus | None = None) -> list[SandboxRecord]:
        """List sandbox rows ordered by ``created_at`` descending.

        When ``status`` is set, only rows with that status are returned.
        """
        with self.session() as session:
            statement = select(SandboxRecord).where(SandboxRecord.project_id == self.project_id)
            if status is not None:
                statement = statement.where(SandboxRecord.status == status)
            statement = statement.order_by(col(SandboxRecord.created_at).desc())
            return list(session.exec(statement).all())

    def update_status(self, id: str, status: SandboxStatus) -> SandboxRecord | None:
        """Update sandbox status and ``updated_at``; return the row or ``None``."""
        with self.session() as session:
            statement = select(SandboxRecord).where(SandboxRecord.id == id, SandboxRecord.project_id == self.project_id)
            record = session.exec(statement).first()
            if record is None:
                return None

            record.status = status
            record.updated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

            return self._commit(session, record, f"Invalid status update constraint: {id}")

    def delete(self, id: str) -> bool:
        """Hard-delete a sandbox metadata row. Returns whether a row was removed."""
        with self.session() as session:
            return self._delete_where(
                session,
                select(SandboxRecord).where(SandboxRecord.id == id, SandboxRecord.project_id == self.project_id),
            )

    def _reconcile_single_record(self, record: SandboxRecord | None) -> SandboxRecord | None:
        """Mark a single record as CLEANED if active and missing on disk."""
        if record is None or record.status is not SandboxStatus.ACTIVE:
            return None
        if Path(record.sandbox_path).is_dir():
            return None
        return self.update_status(record.id, SandboxStatus.CLEANED)

    def reconcile_stale_active(self, id: str | None = None) -> list[SandboxRecord]:
        """Mark active rows whose sandbox directory is missing on disk as cleaned.

        Args:
            id: Optional sandbox ID to restrict reconciliation to. If None, reconciles all active rows.

        Returns:
            List of SandboxRecord rows that were reconciled to CLEANED status.
        """
        if id is not None:
            updated = self._reconcile_single_record(self.get(id))
            return [updated] if updated is not None else []

        reconciled: list[SandboxRecord] = []
        for record in self.list(status=SandboxStatus.ACTIVE):
            updated = self._reconcile_single_record(record)
            if updated is not None:
                reconciled.append(updated)
        return reconciled
