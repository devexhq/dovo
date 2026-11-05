"""ComponentFormatter for WorktreePruneResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.worktree.pruned_item import PrunedItemFormatter
from dovo.cli.ui.formatters.worktree.worktree_views import WorktreePruneView
from dovo.core.worktree.models import WorktreePruneResult


def _format_prune_rich(view: WorktreePruneView) -> RenderableType:
    """Render rich group or text for worktree prune view."""
    if not view.items and not view.errors:
        return Text("No stale worktrees found.")

    renderables: list[RenderableType] = []
    item_formatter = PrunedItemFormatter()
    for item in view.items:
        renderables.append(item_formatter.render_item(item))

    if view.errors:
        for error in view.errors:
            renderables.append(Text(f"Error: {error}", style="red"))

    return Group(*renderables)


class WorktreePruneFormatter(ComponentFormatter[WorktreePruneResult, WorktreePruneView]):
    """Formatter for worktree prune command results."""

    event_type: ClassVar[str] = "worktree.prune"

    def transform(self, data: WorktreePruneResult) -> WorktreePruneView:
        """Derive the presentation-ready view from WorktreePruneResult.

        Args:
            data: Domain WorktreePruneResult instance.

        Returns:
            WorktreePruneView with transformed items and aggregate counts.
        """
        item_formatter = PrunedItemFormatter()
        items = [
            item_formatter.transform(item).model_copy(update={"is_dry_run": True})
            if data.dry_run
            else item_formatter.transform(item)
            for item in data.items
        ]

        return WorktreePruneView(
            **header_fields(data),
            status=data.status,
            dry_run=data.dry_run,
            force=data.force,
            items=items,
            pruned_count=data.pruned_count,
            skipped_count=data.skipped_count,
            failed_count=data.failed_count,
        )

    def to_rich(self, data: WorktreePruneResult) -> RenderableType:
        """Render worktree prune action lines, empty state, or errors, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_format_prune_rich(view), view.diagnostics)
