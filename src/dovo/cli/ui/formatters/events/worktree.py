"""ComponentFormatter for WorktreeLifecycleEvent."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.events import WorktreeLifecycleEvent
from dovo.cli.ui.formatters.base import ComponentFormatter


class WorktreeLifecycleFormatter(ComponentFormatter[WorktreeLifecycleEvent]):
    """Formatter for worktree lifecycle notices."""

    def to_rich(self, data: WorktreeLifecycleEvent) -> Text:
        """Render worktree ready or cleanup notice."""
        if data.action == "ready":
            if data.active:
                return Text(f"Worktree: Active ({data.path})")
            return Text("Worktree: In-place (workspace)")
        if data.action == "cleanup":
            if data.kept and data.worktree_id is not None and data.branch is not None:
                return _render_kept(data)
            if data.kept:
                return Text(f"Worktree: Retained ({data.path})")
            return Text("Worktree: Cleaned")
        return Text(f"Worktree: {data.action} ({data.path})")


def _render_kept(data: WorktreeLifecycleEvent) -> Text:
    """Render `Worktree: Kept at <path> (branch <branch>)` and the inspect/remove `Fix:` line for a kept run worktree."""
    return Text(
        f"Worktree: Kept at {data.path} (branch {data.branch})\n"
        f"Fix: inspect it with `git -C {data.path} status`, remove it with `dovo worktree delete {data.worktree_id}`."
    )
