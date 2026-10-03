"""ComponentFormatter for WorktreeListResult."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.worktree.common import build_worktree_table
from dovo.common.types import ComponentFormatter
from dovo.core.worktree.models import WorktreeListResult


class WorktreeListFormatter(ComponentFormatter[WorktreeListResult]):
    """Formatter for worktree list command results."""

    def to_rich(self, data: WorktreeListResult) -> Any:
        """Render worktree summary list table or empty state."""
        if not data.ok:
            fixes = data.fixes or ["Run `dovo init` to create `.dovo/config.json`"]
            return build_error_panel(
                "Dovo Not Initialized",
                data.errors,
                "Dovo workspace is not initialized.",
                fixes,
            )

        if not data.worktrees:
            return Text("No worktrees found.")

        return build_worktree_table(data.worktrees)
