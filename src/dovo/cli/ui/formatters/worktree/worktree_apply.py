"""ComponentFormatter for WorktreeApplyResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreeApplyView
from dovo.core.worktree.models import WorktreeApplyResult, WorktreeApplyStrategy


def _format_apply_success(view: WorktreeApplyView) -> Group:
    """Render success block for worktree apply."""
    strategy_label = view.strategy.value
    renderables: list[RenderableType] = [
        Text(f"Applied worktree {view.worktree_id} to workspace ({strategy_label})", style="green")
    ]

    if view.strategy == WorktreeApplyStrategy.SQUASH and view.commit_sha:
        renderables.append(Text(f"• Commit: {view.commit_sha}", style="dim"))
    elif view.touched_files:
        files_count = len(view.touched_files)
        files_text = f"{files_count} file changed" if files_count == 1 else f"{files_count} files changed"
        renderables.append(Text(f"• {files_text}", style="dim"))

    renderables.append(Text("• Status updated: merged", style="dim"))

    if view.cleaned_up:
        renderables.append(Text("• worktree and branch deleted", style="dim"))

    for warning in view.warnings:
        renderables.append(Text(f"• {warning}", style="dim"))

    return Group(*renderables)


class WorktreeApplyFormatter(ComponentFormatter[WorktreeApplyResult, WorktreeApplyView]):
    """Formatter for worktree apply command results."""

    event_type: ClassVar[str] = "worktree.apply"

    def transform(self, data: WorktreeApplyResult) -> WorktreeApplyView:
        """Derive the view: the result header, then what the apply changed."""
        return WorktreeApplyView(
            **header_fields(data),
            worktree_id=data.worktree_id,
            strategy=data.strategy,
            touched_files=list(data.touched_files),
            conflicting_files=list(data.conflicting_files),
            cleaned_up=data.cleaned_up,
            commit_sha=data.commit_sha,
        )

    def to_rich(self, data: WorktreeApplyResult) -> RenderableType:
        """Render worktree apply success summary or failure panel, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        if view.ok:
            return with_diagnostics(_format_apply_success(view), view.diagnostics)

        panel = build_error_panel(
            "Worktree Apply Failed",
            view.errors,
            "Worktree apply failed.",
            remediations=view.remediations,
        )
        return with_diagnostics(panel, view.diagnostics)
