"""ComponentFormatter for WorktreeListResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.worktree.common import build_worktree_table
from dovo.core.worktree.models import WorktreeListResult


class WorktreeListFormatter(ComponentFormatter[WorktreeListResult]):
    """Formatter for worktree list command results."""

    def to_rich(self, data: WorktreeListResult) -> RenderableType:
        """Render worktree summary list table or empty state."""
        if not data.worktrees:
            return Text("No worktrees found.")

        return build_worktree_table(data.worktrees)
