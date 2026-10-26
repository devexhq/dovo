"""ComponentFormatter for WorktreeListResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.worktree.common import build_worktree_table
from dovo.core.worktree.models import WorktreeListResult


def _format_list_rich(data: WorktreeListResult) -> RenderableType:
    """Render the error panel, the empty state, or the worktree summary table."""
    if data.errors:
        return build_error_panel(
            "Worktree List Failed", data.errors, "Failed to list worktrees.", remediations=data.remediations
        )
    if not data.worktrees:
        return Text("No worktrees found.")

    return build_worktree_table(data.worktrees)


class WorktreeListFormatter(ComponentFormatter[WorktreeListResult]):
    """Formatter for worktree list command results."""

    def to_rich(self, data: WorktreeListResult) -> RenderableType:
        """Render worktree summary list table, empty state or error, followed by diagnostic_lines(data.diagnostics)."""
        return with_diagnostics(_format_list_rich(data), data.diagnostics)
