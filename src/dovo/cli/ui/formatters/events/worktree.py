"""ComponentFormatter for WorktreeLifecycleEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import WorktreeLifecycleEvent
from dovo.common.types import ComponentFormatter


class WorktreeLifecycleFormatter(ComponentFormatter[WorktreeLifecycleEvent]):
    """Formatter for worktree lifecycle notices."""

    def to_rich(self, data: WorktreeLifecycleEvent) -> Text:
        """Render worktree ready or cleanup notice."""
        if data.action == "ready":
            if data.active:
                return Text(f"Worktree: Active ({data.path})")
            return Text("Worktree: In-place (workspace)")
        if data.action == "cleanup":
            if data.kept:
                return Text(f"Worktree: Retained ({data.path})")
            return Text("Worktree: Cleaned")
        return Text(f"Worktree: {data.action} ({data.path})")
