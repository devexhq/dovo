"""Sandbox delete command handler."""

from __future__ import annotations

import typer

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.sandbox import (
    Sandbox,
    SandboxDeleteResult,
    SandboxDeleteStatus,
    SandboxSession,
)


def _sandbox_delete_confirm_prompt(sandbox: object) -> str:
    """Format confirmation prompt message for deleting a sandbox."""
    branch = getattr(sandbox, "branch_name", getattr(sandbox, "branch", "unknown"))
    path = getattr(sandbox, "sandbox_path", getattr(sandbox, "path", "unknown"))
    s_id = getattr(sandbox, "id", getattr(sandbox, "session_id", "unknown"))
    return f"Delete sandbox '{s_id}' (branch {branch}, path {path})?\nThis removes the git worktree and branch."


def collect_sandbox_delete(
    context: CliContext,
    sandbox_id: str,
) -> SandboxDeleteResult:
    """Load config and look up one sandbox for delete (no mutation)."""
    return Sandbox(context.paths, db=context.db.sandboxes).delete(sandbox_id)


def _confirm_or_abort(row: object) -> bool:
    """Prompt user for confirmation; return True if confirmed."""
    try:
        confirmed = typer.confirm(
            _sandbox_delete_confirm_prompt(row),
            default=False,
        )
    except typer.Abort:
        confirmed = False
    return confirmed


def sandbox_delete_command(
    context: CliContext,
    sandbox_id: str,
    force: bool = False,
    output_format: str = "terminal",
) -> SandboxDeleteResult:
    """Delete a tracked sandbox worktree and branch.

    Confirms before mutating unless ``force`` is True. Already-cleaned rows are
    an idempotent no-op.

    Args:
        context: CLI context instance.
        sandbox_id: Sandbox primary key to delete.
        force: When True, skip the confirmation prompt.
        output_format: Presentation format ("terminal" or "json").
    """
    sandbox = Sandbox(context.paths, db=context.db.sandboxes)
    result = sandbox.delete(sandbox_id)

    if result.status is SandboxDeleteStatus.NOT_INITIALIZED:
        ui_dispatcher.dispatch(result, output_format=output_format)
        return result
    if result.status is SandboxDeleteStatus.NOT_FOUND or result.sandbox is None:
        not_found_result = result.model_copy(update={"errors": [f"Sandbox '{sandbox_id}' not found."]})
        ui_dispatcher.dispatch(not_found_result, output_format=output_format)
        return not_found_result
    if result.status is SandboxDeleteStatus.ALREADY_CLEANED:
        ui_dispatcher.dispatch(result, output_format=output_format)
        return result

    row = result.sandbox

    if not force and not _confirm_or_abort(row):
        aborted = result.model_copy(update={"status": SandboxDeleteStatus.ABORTED, "errors": ["Aborted."]})
        ui_dispatcher.dispatch(aborted, output_format=output_format)
        return aborted

    session = SandboxSession(
        session_id=row.id,
        target_branch=row.branch_name,
        sandbox_path=row.sandbox_path,
        base_commit=row.base_commit,
        name=row.name,
        created_at=row.created_at,
    )
    sandbox.cleanup(session)
    deleted = result.model_copy(update={"status": SandboxDeleteStatus.DELETED, "deleted": True})
    ui_dispatcher.dispatch(deleted, output_format=output_format)
    return deleted
