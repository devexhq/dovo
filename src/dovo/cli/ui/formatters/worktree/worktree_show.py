"""ComponentFormatter for WorktreeShowResult."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.worktree.common import build_worktree_detail_table
from dovo.core.worktree.models import WorktreeShowResult, WorktreeShowStatus


def _format_show_error_panel(data: WorktreeShowResult) -> Panel:
    """Format error panel for non-ok worktree show results."""
    if data.status == WorktreeShowStatus.NOT_FOUND:
        return build_error_panel(
            "Worktree Not Found",
            data.errors,
            "Worktree not found.",
            remediations=data.remediations,
        )

    return build_error_panel(
        "Worktree Show Failed",
        data.errors,
        "Failed to show worktree.",
        remediations=data.remediations,
    )


class WorktreeShowFormatter(ComponentFormatter[WorktreeShowResult]):
    """Formatter for worktree show command results."""

    def to_rich(self, data: WorktreeShowResult) -> RenderableType:
        """Render key-value detail table or error status."""
        if data.ok and data.worktree is not None:
            table = build_worktree_detail_table(data.worktree, disk_present=data.disk_present)
            if data.reconciled:
                return Group(
                    table,
                    Text("Note: worktree directory is missing; status updated to 'cleaned'.", style="dim"),
                )
            return table

        return _format_show_error_panel(data)
