"""ComponentFormatter for WorktreeShowResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.common import build_worktree_detail_table
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreeShowView
from dovo.common.error_codes import ErrorCode
from dovo.core.worktree.models import WorktreeShowResult


def _format_show_error_panel(view: WorktreeShowView) -> Panel:
    """Format error panel for non-ok worktree show results."""
    if view.error_code == ErrorCode.WORKTREE_NOT_FOUND:
        return build_error_panel(
            "Worktree Not Found",
            view.errors,
            "Worktree not found.",
            remediations=view.remediations,
        )

    return build_error_panel(
        "Worktree Show Failed",
        view.errors,
        "Failed to show worktree.",
        remediations=view.remediations,
    )


def _format_show_rich(view: WorktreeShowView) -> RenderableType:
    """Render the key-value detail table, with the reconciled note, or the error panel."""
    if view.ok and view.worktree is not None:
        table = build_worktree_detail_table(view.worktree, disk_present=view.disk_present)
        if view.reconciled:
            return Group(
                table,
                Text("Note: worktree directory is missing; status updated to 'cleaned'.", style="dim"),
            )
        return table

    return _format_show_error_panel(view)


class WorktreeShowFormatter(ComponentFormatter[WorktreeShowResult, WorktreeShowView]):
    """Formatter for worktree show command results."""

    event_type: ClassVar[str] = "worktree.show"

    def transform(self, data: WorktreeShowResult) -> WorktreeShowView:
        """Derive the view: the result header, then the worktree and its disk state."""
        return WorktreeShowView(
            **header_fields(data),
            worktree=data.worktree,
            disk_present=data.disk_present,
            reconciled=data.reconciled,
        )

    def to_rich(self, data: WorktreeShowResult) -> RenderableType:
        """Render key-value detail table or error status, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_format_show_rich(view), view.diagnostics)
