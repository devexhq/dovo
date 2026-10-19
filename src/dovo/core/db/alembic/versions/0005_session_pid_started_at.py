"""Add the pid_started_at column to sessions.

Revision ID: 0005_session_pid_started_at
Revises: 0004_session_discard
Create Date: 2026-10-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from dovo.core.db.migrations import SESSION_DISCARD_REVISION, SESSION_PID_STARTED_AT_REVISION

# revision identifiers, used by Alembic.
revision: str = SESSION_PID_STARTED_AT_REVISION
down_revision: str | None = SESSION_DISCARD_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable pid_started_at column to sessions, leaving existing rows NULL."""
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("pid_started_at", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the pid_started_at column from sessions, preserving all other columns and rows."""
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("pid_started_at")
