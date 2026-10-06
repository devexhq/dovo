"""Rename the runs table, its indexes, and its status check constraint to sessions.

Revision ID: 0002_rename_runs_to_sessions
Revises: 0001_initial_schema
Create Date: 2026-10-05 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

from dovo.core.db.migrations import INITIAL_SCHEMA_REVISION, SESSIONS_RENAME_REVISION

# revision identifiers, used by Alembic.
revision: str = SESSIONS_RENAME_REVISION
down_revision: str | None = INITIAL_SCHEMA_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CHECK_SQL = "status IN ('running', 'completed', 'failed', 'cancelled', 'paused')"
INDEX_RENAMES: tuple[tuple[str, str, str], ...] = (
    ("idx_runs_status", "idx_sessions_status", "status"),
    ("idx_runs_started", "idx_sessions_started", "started_at"),
    ("idx_runs_project_id", "idx_sessions_project_id", "project_id"),
)


def _swap_table(old_table: str, new_table: str, old_check: str, new_check: str) -> None:
    """Rename old_table to new_table and recreate it with new_check in place of old_check."""
    op.rename_table(old_table, new_table)
    with op.batch_alter_table(new_table, recreate="always") as batch_op:
        batch_op.drop_constraint(old_check, type_="check")
        batch_op.create_check_constraint(new_check, STATUS_CHECK_SQL)


def _rename_indexes(table: str, renames: Sequence[tuple[str, str, str]]) -> None:
    """Drop each (old_name, new_name, column) index and recreate it as new_name on table."""
    for old_name, new_name, column in renames:
        op.drop_index(old_name, table_name=table)
        op.create_index(new_name, table, [column], unique=False)


def upgrade() -> None:
    """Rename runs to sessions with idx_sessions_* indexes and ck_sessions_status."""
    _swap_table("runs", "sessions", "ck_runs_status", "ck_sessions_status")
    _rename_indexes("sessions", INDEX_RENAMES)


def downgrade() -> None:
    """Restore runs, idx_runs_* indexes, and ck_runs_status, keeping every row."""
    _swap_table("sessions", "runs", "ck_sessions_status", "ck_runs_status")
    _rename_indexes("runs", tuple((new, old, column) for old, new, column in INDEX_RENAMES))
