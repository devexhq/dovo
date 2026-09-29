"""Repository managing unified blueprint execution tracking CRUD operations using SQLModel."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import col, select

from worktree.core.db.models import RunRecord, RunStatus
from worktree.core.db.repositories.base import BaseRepository


def _coerce_status(status: RunStatus | str | None) -> RunStatus | str | None:
    """Coerce status string to RunStatus enum if valid member, else return as-is."""
    if status is None:
        return None
    return RunStatus(status) if isinstance(status, str) and status in RunStatus._value2member_map_ else status


def _completed_at_for(status: RunStatus, completed_at: str | None) -> str | None:
    """Return completed_at, defaulting to now for terminal statuses."""
    if completed_at is None and status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return completed_at


class RunsRepository(BaseRepository):
    """Repository managing unified blueprint execution tracking CRUD operations using SQLModel."""

    def create(
        self,
        session_id: str,
        blueprint_name: str,
        blueprint_key: str,
        branch_name: str = "",
        status: RunStatus | str = RunStatus.RUNNING,
        pid: int | None = None,
        *,
        blueprint_tier: str | None = None,
        commit_sha: str | None = None,
        use_sandbox: bool = True,
        keep: bool = False,
        agent: str | None = None,
        inputs_json: str | None = None,
        auto_apply: bool = False,
    ) -> RunRecord:
        """Insert a new run record with its resolved run configuration and return the committed instance."""
        status_enum = RunStatus(status) if isinstance(status, str) else status

        record = RunRecord(
            project_id=self.project_id,
            session_id=session_id,
            blueprint_name=blueprint_name,
            blueprint_key=blueprint_key,
            branch_name=branch_name,
            status=status_enum,
            pid=pid,
            blueprint_tier=blueprint_tier,
            commit_sha=commit_sha,
            use_sandbox=use_sandbox,
            keep=keep,
            agent=agent,
            inputs_json=inputs_json,
            auto_apply=auto_apply,
        )

        with self.session() as session:
            return self._commit(
                session,
                record,
                f"Run with session_id '{session_id}' already exists or failed constraints",
            )

    def get(self, session_id: str) -> RunRecord | None:
        """Return the run record matching session_id, or None."""
        with self.session() as session:
            statement = select(RunRecord).where(
                RunRecord.session_id == session_id, RunRecord.project_id == self.project_id
            )
            return session.exec(statement).first()

    def update_status(
        self,
        session_id: str,
        status: RunStatus | str,
        error_message: str | None = None,
        checkpoint_json: str | None = None,
        completed_at: str | None = None,
        pid: int | None = None,
        sandbox_id: str | None = None,
    ) -> RunRecord | None:
        """Update status, optional timestamps, error message, checkpoint JSON, PID, and sandbox id."""
        status_enum = _coerce_status(status)
        if not isinstance(status_enum, RunStatus):
            raise ValueError(f"Invalid status constraint: {status}")

        completed_at = _completed_at_for(status_enum, completed_at)

        with self.session() as session:
            statement = select(RunRecord).where(
                RunRecord.session_id == session_id, RunRecord.project_id == self.project_id
            )
            record = session.exec(statement).first()
            if record is None:
                return None

            record.status = status_enum
            record.completed_at = completed_at
            record.error_message = error_message
            if pid is not None:
                record.pid = pid
            if checkpoint_json is not None:
                record.checkpoint_json = checkpoint_json
            if sandbox_id is not None:
                record.sandbox_id = sandbox_id

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
        status: RunStatus | str | None = None,
        error_message: str | None = None,
        sandbox_id: str | None = None,
    ) -> RunRecord | None:
        """Compare-and-swap the execution-state document at expected_revision, optionally updating lifecycle fields and sandbox id in the same commit; None when no row matches."""
        status_enum = _coerce_status(status)
        if status_enum is not None and not isinstance(status_enum, RunStatus):
            raise ValueError(f"Invalid status constraint: {status}")

        with self.session() as session:
            statement = select(RunRecord).where(
                RunRecord.session_id == session_id,
                RunRecord.project_id == self.project_id,
                RunRecord.execution_state_revision == expected_revision,
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
            if sandbox_id is not None:
                record.sandbox_id = sandbox_id

            return self._commit(
                session,
                record,
                f"Invalid execution state update constraint for session '{session_id}'",
            )

    def save_pause(
        self,
        session_id: str,
        checkpoint_json: str,
        error_message: str | None = None,
    ) -> RunRecord | None:
        """Persist a paused checkpoint without completing the run."""
        return self.update_status(
            session_id=session_id,
            status=RunStatus.PAUSED,
            error_message=error_message,
            checkpoint_json=checkpoint_json,
            completed_at=None,
        )

    def list(
        self,
        limit: int | None = None,
        status: RunStatus | str | None = None,
    ) -> list[RunRecord]:
        """List run records ordered by started_at DESC, id DESC with optional filters."""
        with self.session() as session:
            statement = select(RunRecord).where(RunRecord.project_id == self.project_id)

            status_enum = _coerce_status(status)
            if status_enum is not None:
                statement = statement.where(RunRecord.status == status_enum)

            statement = statement.order_by(col(RunRecord.started_at).desc(), col(RunRecord.id).desc())

            if limit is not None:
                statement = statement.limit(limit)

            return list(session.exec(statement).all())

    def get_latest_paused(self) -> RunRecord | None:
        """Return the most recent run where status == RunStatus.PAUSED, or None."""
        with self.session() as session:
            statement = (
                select(RunRecord)
                .where(RunRecord.status == RunStatus.PAUSED, RunRecord.project_id == self.project_id)
                .order_by(col(RunRecord.started_at).desc(), col(RunRecord.id).desc())
                .limit(1)
            )
            return session.exec(statement).first()
