"""Baseline database schema migration, flattened to the current schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlmodel import AutoString

from dovo.core.db.migrations import INITIAL_SCHEMA_REVISION

# revision identifiers, used by Alembic.
revision: str = INITIAL_SCHEMA_REVISION
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create centralized, project-scoped tables for worktrees, runs, costs, and artifacts."""
    op.create_table(
        "worktrees",
        sa.Column("id", AutoString(), nullable=False),
        sa.Column("project_id", AutoString(), nullable=False),
        sa.Column("name", AutoString(), nullable=True),
        sa.Column("branch_name", AutoString(), nullable=False),
        sa.Column("base_commit", AutoString(), nullable=False),
        sa.Column("worktree_path", AutoString(), nullable=False),
        sa.Column("status", AutoString(), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            AutoString(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            AutoString(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worktree_path"),
        sa.CheckConstraint(
            "status IN ('active', 'merged', 'cleaned', 'conflict')",
            name="ck_worktrees_status",
        ),
    )
    op.create_index("idx_worktrees_status", "worktrees", ["status"], unique=False)
    op.create_index("idx_worktrees_project_id", "worktrees", ["project_id"], unique=False)

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", AutoString(), nullable=False),
        sa.Column("session_id", AutoString(), nullable=False),
        sa.Column("blueprint_key", AutoString(), nullable=False),
        sa.Column("blueprint_name", AutoString(), nullable=False),
        sa.Column("branch_name", AutoString(), nullable=False, server_default=""),
        sa.Column("status", AutoString(), nullable=False, server_default="running"),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            AutoString(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", AutoString(), nullable=True),
        sa.Column("error_message", AutoString(), nullable=True),
        sa.Column("execution_state_json", AutoString(), nullable=True),
        sa.Column("execution_state_revision", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("blueprint_tier", AutoString(), nullable=True),
        sa.Column("commit_sha", AutoString(), nullable=True),
        sa.Column("use_worktree", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("keep", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("agent", AutoString(), nullable=True),
        sa.Column("inputs_json", AutoString(), nullable=True),
        sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("worktree_id", AutoString(), nullable=True),
        sa.Column("worktree_kept", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled', 'paused')",
            name="ck_runs_status",
        ),
    )
    op.create_index("idx_runs_status", "runs", ["status"], unique=False)
    op.create_index("idx_runs_started", "runs", ["started_at"], unique=False)
    op.create_index("idx_runs_project_id", "runs", ["project_id"], unique=False)

    op.create_table(
        "costs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", AutoString(), nullable=False),
        sa.Column("session_id", AutoString(), nullable=False),
        sa.Column("branch_name", AutoString(), nullable=False),
        sa.Column("model_id", AutoString(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_usd_cost", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "created_at",
            AutoString(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_costs_session", "costs", ["session_id"], unique=False)
    op.create_index("idx_costs_created", "costs", ["created_at"], unique=False)
    op.create_index("idx_costs_project_id", "costs", ["project_id"], unique=False)

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
    """Drop all tables created in the baseline migration."""
    op.drop_index("idx_artifacts_expires_at", table_name="artifacts")
    op.drop_index("idx_artifacts_session_id", table_name="artifacts")
    op.drop_index("idx_artifacts_project_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("idx_costs_project_id", table_name="costs")
    op.drop_index("idx_costs_created", table_name="costs")
    op.drop_index("idx_costs_session", table_name="costs")
    op.drop_table("costs")

    op.drop_index("idx_runs_project_id", table_name="runs")
    op.drop_index("idx_runs_started", table_name="runs")
    op.drop_index("idx_runs_status", table_name="runs")
    op.drop_table("runs")

    op.drop_index("idx_worktrees_project_id", table_name="worktrees")
    op.drop_index("idx_worktrees_status", table_name="worktrees")
    op.drop_table("worktrees")
