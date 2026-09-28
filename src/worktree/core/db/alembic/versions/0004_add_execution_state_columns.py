"""Add execution-state and run-configuration columns to runs; migrate legacy paused checkpoints.

Revision ID: 0004_add_execution_state_columns
Revises: 0003_add_artifacts_table
Create Date: 2026-09-28 00:00:00.000000

"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlmodel import AutoString

# revision identifiers, used by Alembic.
revision: str = "0004_add_execution_state_columns"
down_revision: str | None = "0003_add_artifacts_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the 10 execution-state and run-configuration columns to runs, then backfill legacy paused checkpoints."""
    op.add_column("runs", sa.Column("execution_state_json", AutoString(), nullable=True))
    op.add_column("runs", sa.Column("execution_state_revision", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("runs", sa.Column("blueprint_tier", AutoString(), nullable=True))
    op.add_column("runs", sa.Column("commit_sha", AutoString(), nullable=True))
    op.add_column("runs", sa.Column("use_sandbox", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    op.add_column("runs", sa.Column("keep", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("runs", sa.Column("agent", AutoString(), nullable=True))
    op.add_column("runs", sa.Column("inputs_json", AutoString(), nullable=True))
    op.add_column("runs", sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    op.add_column("runs", sa.Column("sandbox_id", AutoString(), nullable=True))

    _migrate_paused_runs(op.get_bind())


def downgrade() -> None:
    """Drop the 10 execution-state and run-configuration columns from runs."""
    op.drop_column("runs", "sandbox_id")
    op.drop_column("runs", "auto_apply")
    op.drop_column("runs", "inputs_json")
    op.drop_column("runs", "agent")
    op.drop_column("runs", "keep")
    op.drop_column("runs", "use_sandbox")
    op.drop_column("runs", "commit_sha")
    op.drop_column("runs", "blueprint_tier")
    op.drop_column("runs", "execution_state_revision")
    op.drop_column("runs", "execution_state_json")


def convert_checkpoint_to_state_tree(checkpoint_raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert a legacy checkpoint JSON string into (scalar_column_kwargs, execution_state_json_dict).

    Returns ({}, {}) if checkpoint_raw cannot be parsed.
    """
    try:
        checkpoint = json.loads(checkpoint_raw)
        scalar_column_kwargs = _build_scalar_columns(checkpoint)
        execution_state_json_dict = _build_execution_state_json(
            checkpoint, checkpoint["next_step_index"], checkpoint["pending_step_id"]
        )
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return {}, {}

    return scalar_column_kwargs, execution_state_json_dict


def _build_scalar_columns(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Extract use_sandbox, keep, agent, inputs_json, blueprint_tier, and sandbox_id from a parsed checkpoint."""
    identity = checkpoint.get("identity") or {}
    return {
        "use_sandbox": checkpoint.get("use_sandbox") is not False,
        "keep": checkpoint.get("keep") is True,
        "agent": checkpoint.get("agent"),
        "inputs_json": json.dumps(checkpoint.get("inputs", {})),
        "blueprint_tier": None,
        "sandbox_id": checkpoint.get("sandbox_id"),
    } | ({"blueprint_tier": identity.get("blueprint_key")} if identity.get("blueprint_key") else {})


def _build_execution_state_json(
    checkpoint: dict[str, Any], next_step_index: int, pending_step_id: str
) -> dict[str, Any]:
    """Assemble the versioned execution_state_json document from a parsed legacy checkpoint."""
    return {
        "schema_version": 1,
        "revision": 1,
        "manifest": _build_manifest(checkpoint),
        "nodes": [
            *_build_completed_nodes(checkpoint, next_step_index),
            _build_pending_node(checkpoint, pending_step_id),
        ],
    }


def _build_manifest(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Build the manifest block from checkpoint identity, when present."""
    blueprint_key = (checkpoint.get("identity") or {}).get("blueprint_key", "")
    ref = f"repo:blueprint:{blueprint_key}" if blueprint_key else ""
    return {"blueprint": {"ref": ref, "sha": ""}, "steps": []}


def _build_completed_nodes(checkpoint: dict[str, Any], next_step_index: int) -> list[dict[str, Any]]:
    """Build one completed kind='step' node per checkpoint step_result before next_step_index."""
    step_results = checkpoint.get("step_results", [])[:next_step_index]
    return [_build_step_node(result, "completed") for result in step_results]


def _build_pending_node(checkpoint: dict[str, Any], pending_step_id: str) -> dict[str, Any]:
    """Build the single paused kind='step' node for the checkpoint's pending step."""
    pending_result = checkpoint.get("pending_result")
    if pending_result is None:
        return {"kind": "step", "id": pending_step_id, "state": "paused", "attempts": []}
    return _build_step_node(pending_result, "paused", step_id=pending_step_id)


def _build_step_node(result: dict[str, Any], state: str, step_id: str | None = None) -> dict[str, Any]:
    """Build a kind='step' node wrapping one synthesized attempt record around a StepResult dict."""
    return {
        "kind": "step",
        "id": step_id or result["step_id"],
        "state": state,
        "attempts": [
            {
                "number": result.get("attempts", 1),
                "started_at": None,
                "completed_at": None,
                "result": result,
            }
        ],
    }


def _migrate_paused_runs(bind: Connection) -> None:
    """Backfill execution_state_json and scalar config columns for every paused run's checkpoint."""
    rows = bind.execute(sa.text("SELECT id, checkpoint_json FROM runs WHERE status = 'paused'")).fetchall()
    for row in rows:
        _apply_conversion(bind, row.id, row.checkpoint_json)


def _apply_conversion(bind: Connection, run_id: int, checkpoint_raw: str | None) -> None:
    """Convert one paused run's checkpoint and persist the resulting columns, or leave the row untouched."""
    if checkpoint_raw is None:
        return

    scalar_column_kwargs, execution_state_json_dict = convert_checkpoint_to_state_tree(checkpoint_raw)
    if not execution_state_json_dict:
        return

    bind.execute(
        sa.text(
            "UPDATE runs SET execution_state_json = :execution_state_json, execution_state_revision = 1, "
            "use_sandbox = :use_sandbox, keep = :keep, agent = :agent, inputs_json = :inputs_json, "
            "blueprint_tier = :blueprint_tier, sandbox_id = :sandbox_id WHERE id = :run_id"
        ),
        {
            "execution_state_json": json.dumps(execution_state_json_dict),
            "run_id": run_id,
            **scalar_column_kwargs,
        },
    )
