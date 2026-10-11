"""Add artifact revisions: revision, digest and provenance columns, and a revision-wide unique constraint.

Revision ID: 0003_artifact_revisions
Revises: 0002_rename_runs_to_sessions
Create Date: 2026-10-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlmodel import AutoString

from dovo.core.db.migrations import ARTIFACT_REVISIONS_REVISION, SESSIONS_RENAME_REVISION

# revision identifiers, used by Alembic.
revision: str = ARTIFACT_REVISIONS_REVISION
down_revision: str | None = SESSIONS_RENAME_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNIQUE = "uq_artifacts_project_session_name"
NEW_UNIQUE = "uq_artifacts_project_session_name_revision"


def upgrade() -> None:
    """Add revision, digest and provenance columns; backfill revision 1; widen the unique constraint to include revision."""
    with op.batch_alter_table("artifacts", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("digest", AutoString(), nullable=True))
        batch_op.add_column(sa.Column("step_id", AutoString(), nullable=True))
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("loop_iteration", sa.Integer(), nullable=True))
        batch_op.drop_constraint(OLD_UNIQUE, type_="unique")
        batch_op.create_unique_constraint(NEW_UNIQUE, ["project_id", "session_id", "name", "revision"])


def downgrade() -> None:
    """Keep only the highest revision per (project_id, session_id, name), drop the new columns, and restore the old unique constraint."""
    op.execute(
        "DELETE FROM artifacts WHERE revision < ("
        "SELECT MAX(newer.revision) FROM artifacts AS newer "
        "WHERE newer.project_id = artifacts.project_id "
        "AND newer.session_id = artifacts.session_id "
        "AND newer.name = artifacts.name)"
    )
    with op.batch_alter_table("artifacts", recreate="always") as batch_op:
        batch_op.drop_constraint(NEW_UNIQUE, type_="unique")
        batch_op.drop_column("loop_iteration")
        batch_op.drop_column("attempt")
        batch_op.drop_column("step_id")
        batch_op.drop_column("digest")
        batch_op.drop_column("revision")
        batch_op.create_unique_constraint(OLD_UNIQUE, ["project_id", "session_id", "name"])
