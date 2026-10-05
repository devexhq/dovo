"""Repository managing session record CRUD operations using SQLModel."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import col, select

from dovo.core.db.models import SessionRecord, SessionStatus
from dovo.core.db.repositories.base import BaseRepository


def _coerce_status(status: SessionStatus | str | None) -> SessionStatus | str | None:
    """Coerce status string to SessionStatus enum if valid member, else return as-is."""
    if status is None:
        return None
    return SessionStatus(status) if isinstance(status, str) and status in SessionStatus._value2member_map_ else status


def _completed_at_for(status: SessionStatus, completed_at: str | None) -> str | None:
    """Return completed_at, defaulting to now for terminal statuses."""
    if completed_at is None and status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED):
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return completed_at


class SessionsRepository(BaseRepository):
    """Repository managing session record CRUD operations using SQLModel."""

    def create(
        self,
        session_id: str,
        blueprint_name: str,
        blueprint_key: str,
        branch_name: str = "",
        status: SessionStatus | str = SessionStatus.RUNNING,
        pid: int | None = None,
        *,
        blueprint_tier: str | None = None,
        commit_sha: str | None = None,
        use_worktree: bool = True,
        keep: bool = False,
        agent: str | None = None,
        inputs_json: str | None = None,
        auto_apply: bool = False,
    ) -> SessionRecord:
        """Insert a new session record with its resolved run configuration and return the committed instance."""
        status_enum = SessionStatus(status) if isinstance(status, str) else status

        record = SessionRecord(
            project_id=self.project_id,
            session_id=session_id,
            blueprint_name=blueprint_name,
            blueprint_key=blueprint_key,
            branch_name=branch_name,
            status=status_enum,
            pid=pid,
            blueprint_tier=blueprint_tier,
            commit_sha=commit_sha,
            use_worktree=use_worktree,
            keep=keep,
            agent=agent,
            inputs_json=inputs_json,
            auto_apply=auto_apply,
        )

        with self.session() as session:
            return self._commit(
                session,
                record,
                f"Session with session_id '{session_id}' already exists or failed constraints",
            )

    def get(self, session_id: str) -> SessionRecord | None:
        """Return the session record matching session_id, or None."""
        with self.session() as session:
            statement = select(SessionRecord).where(
                SessionRecord.session_id == session_id, SessionRecord.project_id == self.project_id
            )
            return session.exec(statement).first()

    def update_status(
        self,
        session_id: str,
        status: SessionStatus | str,
        error_message: str | None = None,
        completed_at: str | None = None,
        pid: int | None = None,
        worktree_id: str | None = None,
        worktree_kept: bool | None = None,
    ) -> SessionRecord | None:
        """Update status, optional timestamps, error message, PID, worktree id, and worktree-kept outcome."""
        status_enum = _coerce_status(status)
        if not isinstance(status_enum, SessionStatus):
            raise ValueError(f"Invalid status constraint: {status}")

        completed_at = _completed_at_for(status_enum, completed_at)

        with self.session() as session:
            statement = select(SessionRecord).where(
                SessionRecord.session_id == session_id, SessionRecord.project_id == self.project_id
            )
            record = session.exec(statement).first()
            if record is None:
                return None

            record.status = status_enum
            record.completed_at = completed_at
            record.error_message = error_message
            if pid is not None:
                record.pid = pid
            if worktree_id is not None:
                record.worktree_id = worktree_id
            if worktree_kept is not None:
                record.worktree_kept = worktree_kept

            return self._commit(
                session,
                record,
                f"Invalid status update constraint for session '{session_id}'",
            )

    def save_execution_state(
        self,
        session_id: str,
        execution_state_json: str,
        *,
        expected_revision: int,
        next_revision: int,
        status: SessionStatus | str | None = None,
        error_message: str | None = None,
        worktree_id: str | None = None,
        worktree_kept: bool | None = None,
    ) -> SessionRecord | None:
        """Compare-and-swap the execution-state document at expected_revision, optionally updating lifecycle fields, worktree id, and worktree-kept outcome in the same commit; None when no row matches."""
        status_enum = _coerce_status(status)
        if status_enum is not None and not isinstance(status_enum, SessionStatus):
            raise ValueError(f"Invalid status constraint: {status}")

        with self.session() as session:
            statement = select(SessionRecord).where(
                SessionRecord.session_id == session_id,
                SessionRecord.project_id == self.project_id,
                SessionRecord.execution_state_revision == expected_revision,
            )
            record = session.exec(statement).first()
            if record is None:
                return None

            record.execution_state_json = execution_state_json
            record.execution_state_revision = next_revision
            if status_enum is not None:
                record.status = status_enum
                record.completed_at = _completed_at_for(status_enum, None)
                record.error_message = error_message
            if worktree_id is not None:
                record.worktree_id = worktree_id
            if worktree_kept is not None:
                record.worktree_kept = worktree_kept

            return self._commit(
                session,
                record,
                f"Invalid execution state update constraint for session '{session_id}'",
            )

    def list(
        self,
        limit: int | None = None,
        status: SessionStatus | str | None = None,
    ) -> list[SessionRecord]:
        """List session records ordered by started_at DESC, id DESC with optional filters."""
        with self.session() as session:
            statement = select(SessionRecord).where(SessionRecord.project_id == self.project_id)

            status_enum = _coerce_status(status)
            if status_enum is not None:
                statement = statement.where(SessionRecord.status == status_enum)

            statement = statement.order_by(col(SessionRecord.started_at).desc(), col(SessionRecord.id).desc())

            if limit is not None:
                statement = statement.limit(limit)

            return list(session.exec(statement).all())

    def get_latest_paused(self) -> SessionRecord | None:
        """Return the most recent session where status == SessionStatus.PAUSED, or None."""
        with self.session() as session:
            statement = (
                select(SessionRecord)
                .where(SessionRecord.status == SessionStatus.PAUSED, SessionRecord.project_id == self.project_id)
                .order_by(col(SessionRecord.started_at).desc(), col(SessionRecord.id).desc())
                .limit(1)
            )
            return session.exec(statement).first()
