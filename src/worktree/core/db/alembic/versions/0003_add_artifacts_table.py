"""Add the artifacts table.

Revision ID: 0003_add_artifacts_table
Revises: 0002_drop_catalog_table
Create Date: 2026-09-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlmodel import AutoString

# revision identifiers, used by Alembic.
revision: str = "0003_add_artifacts_table"
down_revision: str | None = "0002_drop_catalog_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the artifacts table with its project/session/name unique constraint and expires_at index."""
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", AutoString(), nullable=False),
        sa.Column("session_id", AutoString(), nullable=False),
        sa.Column("name", AutoString(), nullable=False),
        sa.Column("path", AutoString(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            AutoString(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "session_id", "name", name="uq_artifacts_project_session_name"),
    )
    op.create_index("idx_artifacts_project_id", "artifacts", ["project_id"], unique=False)
    op.create_index("idx_artifacts_session_id", "artifacts", ["session_id"], unique=False)
    op.create_index("idx_artifacts_expires_at", "artifacts", ["expires_at"], unique=False)


def downgrade() -> None:
    """Drop the artifacts table."""
    op.drop_index("idx_artifacts_expires_at", table_name="artifacts")
    op.drop_index("idx_artifacts_session_id", table_name="artifacts")
    op.drop_index("idx_artifacts_project_id", table_name="artifacts")
    op.drop_table("artifacts")
