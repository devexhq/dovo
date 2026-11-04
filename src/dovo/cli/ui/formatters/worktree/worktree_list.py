"""ComponentFormatter for WorktreeListResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.common import build_worktree_table
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreeListView
from dovo.core.worktree.models import WorktreeListResult


def _format_list_rich(view: WorktreeListView) -> RenderableType:
    """Render the error panel, the empty state, or the worktree summary table."""
    if not view.ok:
        return build_error_panel(
            "Worktree List Failed", view.errors, "Failed to list worktrees.", remediations=view.remediations
        )
    if not view.worktrees:
        return Text("No worktrees found.")

    return build_worktree_table(view.worktrees)


class WorktreeListFormatter(ComponentFormatter[WorktreeListResult, WorktreeListView]):
    """Formatter for worktree list command results."""

    def transform(self, data: WorktreeListResult) -> WorktreeListView:
        """Derive the view: the result header, then every worktree record."""
        return WorktreeListView(**header_fields(data), worktrees=list(data.worktrees))

    def to_rich(self, data: WorktreeListResult) -> RenderableType:
        """Render worktree summary list table, empty state or error, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_format_list_rich(view), view.diagnostics)
