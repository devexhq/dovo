"""ComponentFormatter for WorktreeDeleteResult."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.common.error_codes import ErrorCode
from dovo.core.worktree.models import WorktreeDeleteResult


def _format_delete_error_panel(data: WorktreeDeleteResult) -> Panel:
    """Format error panel for non-ok worktree delete results."""
    if data.error_code == ErrorCode.WORKTREE_NOT_FOUND:
        return build_error_panel(
            "Worktree Not Found",
            data.errors,
            f"Worktree '{data.worktree_id}' not found.",
            remediations=data.remediations,
        )
    return build_error_panel(
        "Worktree Delete Failed",
        data.errors,
        "Worktree delete failed.",
        remediations=data.remediations,
    )


def _format_delete_rich(data: WorktreeDeleteResult) -> RenderableType:
    """Render the already-cleaned, cancelled, deleted or error state."""
    if data.already_cleaned:
        return Text(f"Worktree '{data.worktree_id}' is already cleaned; nothing to remove.")
    if data.cancelled:
        return Text("Deletion cancelled.")
    if data.deleted:
        return Group(
            Text(f"Worktree deleted: {data.worktree_id}", style="green"),
            *(Text(f"   • {warning}", style="dim") for warning in data.warnings),
        )
    return _format_delete_error_panel(data)


class WorktreeDeleteFormatter(ComponentFormatter[WorktreeDeleteResult]):
    """Formatter for worktree delete command results."""

    def to_rich(self, data: WorktreeDeleteResult) -> RenderableType:
        """Render the deleted, already-cleaned, cancelled or error state, followed by diagnostic_lines(data.diagnostics)."""
        return with_diagnostics(_format_delete_rich(data), data.diagnostics)
