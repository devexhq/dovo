"""Add the discard column to sessions.

Revision ID: 0004_session_discard
Revises: 0003_artifact_revisions
Create Date: 2026-10-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from dovo.core.db.migrations import ARTIFACT_REVISIONS_REVISION, SESSION_DISCARD_REVISION

# revision identifiers, used by Alembic.
revision: str = SESSION_DISCARD_REVISION
down_revision: str | None = ARTIFACT_REVISIONS_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the discard column to sessions, defaulting existing rows to false."""
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("discard", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    """Drop the discard column from sessions, preserving all other columns and rows."""
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("discard")
