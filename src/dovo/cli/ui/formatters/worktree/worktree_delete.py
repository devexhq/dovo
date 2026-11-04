"""ComponentFormatter for WorktreeDeleteResult."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreeDeleteView
from dovo.common.error_codes import ErrorCode
from dovo.core.worktree.models import WorktreeDeleteResult


def _format_delete_error_panel(view: WorktreeDeleteView) -> Panel:
    """Format error panel for non-ok worktree delete results."""
    if view.error_code == ErrorCode.WORKTREE_NOT_FOUND:
        return build_error_panel(
            "Worktree Not Found",
            view.errors,
            f"Worktree '{view.worktree_id}' not found.",
            remediations=view.remediations,
        )
    return build_error_panel(
        "Worktree Delete Failed",
        view.errors,
        "Worktree delete failed.",
        remediations=view.remediations,
    )


def _format_delete_rich(view: WorktreeDeleteView) -> RenderableType:
    """Render the already-cleaned, cancelled, deleted or error state."""
    if view.already_cleaned:
        return Text(f"Worktree '{view.worktree_id}' is already cleaned; nothing to remove.")
    if view.cancelled:
        return Text("Deletion cancelled.")
    if view.deleted:
        return Group(
            Text(f"Worktree deleted: {view.worktree_id}", style="green"),
            *(Text(f"   • {warning}", style="dim") for warning in view.warnings),
        )
    return _format_delete_error_panel(view)


class WorktreeDeleteFormatter(ComponentFormatter[WorktreeDeleteResult, WorktreeDeleteView]):
    """Formatter for worktree delete command results."""

    def transform(self, data: WorktreeDeleteResult) -> WorktreeDeleteView:
        """Derive the view: the result header, then what the delete did."""
        return WorktreeDeleteView(
            **header_fields(data),
            worktree_id=data.worktree_id,
            worktree=data.worktree,
            deleted=data.deleted,
            already_cleaned=data.already_cleaned,
            cancelled=data.cancelled,
        )

    def to_rich(self, data: WorktreeDeleteResult) -> RenderableType:
        """Render the deleted, already-cleaned, cancelled or error state, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_format_delete_rich(view), view.diagnostics)
