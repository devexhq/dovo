"""ComponentFormatter for WorktreeShowResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.worktree.common import build_worktree_detail_table
from dovo.common.types import ComponentFormatter
from dovo.core.worktree.models import WorktreeShowResult, WorktreeShowStatus


def _format_show_error_panel(data: WorktreeShowResult) -> Panel:
    """Format error panel for non-ok worktree show results."""
    if data.status == WorktreeShowStatus.NOT_FOUND:
        fixes = data.fixes or ["Run `dovo worktree list` to see known worktrees"]
        return build_error_panel(
            "Worktree Not Found",
            data.errors,
            "Worktree not found.",
            fixes,
        )

    return build_error_panel(
        "Worktree Show Failed",
        data.errors,
        "Failed to show worktree.",
        data.fixes,
    )


class WorktreeShowFormatter(ComponentFormatter[WorktreeShowResult]):
    """Formatter for worktree show command results."""

    def to_rich(self, data: WorktreeShowResult) -> Any:
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
