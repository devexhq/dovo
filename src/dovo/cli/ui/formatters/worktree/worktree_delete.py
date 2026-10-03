"""ComponentFormatter for WorktreeDeleteResult."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.common.types import ComponentFormatter
from dovo.core.worktree.models import WorktreeDeleteResult, WorktreeDeleteStatus


def _format_delete_error_panel(data: WorktreeDeleteResult) -> Panel:
    """Format error panel for non-ok worktree delete results."""
    if data.status == WorktreeDeleteStatus.NOT_INITIALIZED:
        fixes = data.fixes or ["Run `dovo init` to create `.dovo/config.json`"]
        return build_error_panel(
            "Dovo Not Initialized",
            data.errors,
            "Dovo workspace is not initialized.",
            fixes,
        )
    if data.status == WorktreeDeleteStatus.NOT_FOUND:
        fixes = data.fixes or ["Run `dovo worktree list` to see known worktrees"]
        return build_error_panel(
            "Worktree Not Found",
            data.errors,
            f"Worktree '{data.worktree_id}' not found.",
            fixes,
        )
    return build_error_panel(
        "Worktree Delete Failed",
        data.errors,
        "Worktree delete failed.",
        data.fixes,
    )


class WorktreeDeleteFormatter(ComponentFormatter[WorktreeDeleteResult]):
    """Formatter for worktree delete command results."""

    def to_rich(self, data: WorktreeDeleteResult) -> Any:
        """Render worktree delete success, aborted, or error state."""
        if data.status == WorktreeDeleteStatus.ALREADY_CLEANED:
            return Text(f"Worktree '{data.worktree_id}' is already cleaned; nothing to remove.")
        if data.status == WorktreeDeleteStatus.ABORTED:
            return Text("Aborted.")
        if data.status == WorktreeDeleteStatus.DELETED or data.deleted:
            return Text(f"Worktree deleted: {data.worktree_id}", style="green")
        return _format_delete_error_panel(data)
