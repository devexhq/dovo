"""Repository managing worktree metadata CRUD operations using SQLModel."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import col, select

from dovo.core.db.models import WorktreeRecord, WorktreeStatus
from dovo.core.db.repositories.base import BaseRepository


class WorktreesRepository(BaseRepository):
    """Repository managing worktree metadata CRUD operations using SQLModel."""

    def create(
        self,
        id: str,
        branch_name: str,
        base_commit: str,
        worktree_path: Path | str,
        name: str | None = None,
    ) -> WorktreeRecord:
        """Create a worktree metadata row with status ``active``.

        Returns:
            The created `WorktreeRecord`, including DB-assigned timestamps.

        Raises:
            ValueError: If a row with the same ``id`` already exists.
        """
        record = WorktreeRecord(
            id=id,
            project_id=self.project_id,
            name=name,
            branch_name=branch_name,
            base_commit=base_commit,
            worktree_path=Path(str(worktree_path)),
            status=WorktreeStatus.ACTIVE,
        )

        with self.session() as session:
            return self._commit(session, record, f"Worktree with id '{id}' already exists")

    def get(self, id: str) -> WorktreeRecord | None:
        """Return the worktree row for ``id``, or ``None`` when missing."""
        with self.session() as session:
            statement = select(WorktreeRecord).where(
                WorktreeRecord.id == id, WorktreeRecord.project_id == self.project_id
            )
            return session.exec(statement).first()

    def list(self, status: WorktreeStatus | None = None) -> list[WorktreeRecord]:
        """List worktree rows ordered by ``created_at`` descending.

        When ``status`` is set, only rows with that status are returned.
        """
        with self.session() as session:
            statement = select(WorktreeRecord).where(WorktreeRecord.project_id == self.project_id)
            if status is not None:
                statement = statement.where(WorktreeRecord.status == status)
            statement = statement.order_by(col(WorktreeRecord.created_at).desc())
            return list(session.exec(statement).all())

    def update_status(self, id: str, status: WorktreeStatus) -> WorktreeRecord | None:
        """Update worktree status and ``updated_at``; return the row or ``None``."""
        with self.session() as session:
            statement = select(WorktreeRecord).where(
                WorktreeRecord.id == id, WorktreeRecord.project_id == self.project_id
            )
            record = session.exec(statement).first()
            if record is None:
                return None

            record.status = status
            record.updated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

            return self._commit(session, record, f"Invalid status update constraint: {id}")

    def delete(self, id: str) -> bool:
        """Hard-delete a worktree metadata row. Returns whether a row was removed."""
        with self.session() as session:
            return self._delete_where(
                session,
                select(WorktreeRecord).where(WorktreeRecord.id == id, WorktreeRecord.project_id == self.project_id),
            )

    def _reconcile_single_record(self, record: WorktreeRecord | None) -> WorktreeRecord | None:
        """Mark a single record as CLEANED if active and missing on disk."""
        if record is None or record.status is not WorktreeStatus.ACTIVE:
            return None
        if Path(record.worktree_path).is_dir():
            return None
        return self.update_status(record.id, WorktreeStatus.CLEANED)

    def reconcile_stale_active(self, id: str | None = None) -> list[WorktreeRecord]:
        """Mark active rows whose worktree directory is missing on disk as cleaned.

        Args:
            id: Optional worktree ID to restrict reconciliation to. If None, reconciles all active rows.

        Returns:
            List of WorktreeRecord rows that were reconciled to CLEANED status.
        """
        if id is not None:
            updated = self._reconcile_single_record(self.get(id))
            return [updated] if updated is not None else []

        reconciled: list[WorktreeRecord] = []
        for record in self.list(status=WorktreeStatus.ACTIVE):
            updated = self._reconcile_single_record(record)
            if updated is not None:
                reconciled.append(updated)
        return reconciled
