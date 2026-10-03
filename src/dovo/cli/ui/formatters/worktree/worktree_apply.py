"""ComponentFormatter for WorktreeApplyResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.worktree.models import WorktreeApplyResult, WorktreeApplyStrategy


def _format_apply_success(data: WorktreeApplyResult) -> Group:
    """Render success block for worktree apply."""
    strategy_label = data.strategy.value
    renderables: list[Any] = [
        Text(f"Applied worktree {data.worktree_id} to workspace ({strategy_label})", style="green")
    ]

    if data.strategy == WorktreeApplyStrategy.SQUASH and data.commit_sha:
        renderables.append(Text(f"• Commit: {data.commit_sha}", style="dim"))
    elif data.touched_files:
        files_count = len(data.touched_files)
        files_text = f"{files_count} file changed" if files_count == 1 else f"{files_count} files changed"
        renderables.append(Text(f"• {files_text}", style="dim"))

    renderables.append(Text("• Status updated: merged", style="dim"))

    if data.cleaned_up:
        renderables.append(Text("• worktree and branch deleted", style="dim"))

    for warning in data.warnings:
        renderables.append(Text(f"• {warning}", style="dim"))

    return Group(*renderables)


class WorktreeApplyFormatter(ComponentFormatter[WorktreeApplyResult]):
    """Formatter for worktree apply command results."""

    def to_rich(self, data: WorktreeApplyResult) -> Any:
        """Render worktree apply success summary or failure panel."""
        if data.ok:
            return _format_apply_success(data)

        return build_error_panel(
            "Worktree Apply Failed",
            data.errors,
            "Worktree apply failed.",
            data.fixes,
        )
