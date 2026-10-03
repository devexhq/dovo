"""Worktree delete command handler."""

from __future__ import annotations

import typer

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.worktree import (
    Worktree,
    WorktreeDeleteResult,
    WorktreeDeleteStatus,
    WorktreeSession,
)


def _worktree_delete_confirm_prompt(worktree: object) -> str:
    """Format confirmation prompt message for deleting a worktree."""
    branch = getattr(worktree, "branch_name", getattr(worktree, "branch", "unknown"))
    path = getattr(worktree, "worktree_path", getattr(worktree, "path", "unknown"))
    s_id = getattr(worktree, "id", getattr(worktree, "session_id", "unknown"))
    return f"Delete worktree '{s_id}' (branch {branch}, path {path})?\nThis removes the git worktree and branch."


def collect_worktree_delete(
    context: CliContext,
    worktree_id: str,
) -> WorktreeDeleteResult:
    """Load config and look up one worktree for delete (no mutation)."""
    return Worktree(context.paths, db=context.db.worktrees).delete(worktree_id)


def _confirm_or_abort(row: object) -> bool:
    """Prompt user for confirmation; return True if confirmed."""
    try:
        confirmed = typer.confirm(
            _worktree_delete_confirm_prompt(row),
            default=False,
        )
    except typer.Abort:
        confirmed = False
    return confirmed


def worktree_delete_command(
    context: CliContext,
    worktree_id: str,
    force: bool = False,
    output_format: str = "terminal",
) -> WorktreeDeleteResult:
    """Delete a tracked worktree and branch.

    Confirms before mutating unless ``force`` is True. Already-cleaned rows are
    an idempotent no-op.

    Args:
        context: CLI context instance.
        worktree_id: Worktree primary key to delete.
        force: When True, skip the confirmation prompt.
        output_format: Presentation format ("terminal" or "json").
    """
    worktree = Worktree(context.paths, db=context.db.worktrees)
    result = worktree.delete(worktree_id)

    if result.status is WorktreeDeleteStatus.NOT_INITIALIZED:
        ui_dispatcher.dispatch(result, output_format=output_format)
        return result
    if result.status is WorktreeDeleteStatus.NOT_FOUND or result.worktree is None:
        not_found_result = result.model_copy(update={"errors": [f"Worktree '{worktree_id}' not found."]})
        ui_dispatcher.dispatch(not_found_result, output_format=output_format)
        return not_found_result
    if result.status is WorktreeDeleteStatus.ALREADY_CLEANED:
        ui_dispatcher.dispatch(result, output_format=output_format)
        return result

    row = result.worktree

    if not force and not _confirm_or_abort(row):
        aborted = result.model_copy(update={"status": WorktreeDeleteStatus.ABORTED, "errors": ["Aborted."]})
        ui_dispatcher.dispatch(aborted, output_format=output_format)
        return aborted

    session = WorktreeSession(
        session_id=row.id,
        target_branch=row.branch_name,
        worktree_path=row.worktree_path,
        base_commit=row.base_commit,
        name=row.name,
        created_at=row.created_at,
    )
    worktree.cleanup(session)
    deleted = result.model_copy(update={"status": WorktreeDeleteStatus.DELETED, "deleted": True})
    ui_dispatcher.dispatch(deleted, output_format=output_format)
    return deleted
