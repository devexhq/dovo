"""ComponentFormatter for SandboxPruneResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.sandbox.pruned_item import PrunedItemFormatter
from dovo.cli.ui.formatters.sandbox.sandbox_views import SandboxPruneView
from dovo.common.types import ComponentFormatter
from dovo.core.sandbox.models import SandboxPruneResult


def _format_prune_rich(view: SandboxPruneView) -> Any:
    """Render rich group or text for sandbox prune view."""
    if not view.items and not view.errors:
        return Text("No stale sandboxes found.")

    renderables: list[Any] = []
    item_formatter = PrunedItemFormatter()
    for item in view.items:
        renderables.append(item_formatter.render_item(item))

    if view.errors:
        for error in view.errors:
            renderables.append(Text(f"Error: {error}", style="red"))

    return Group(*renderables)


class SandboxPruneFormatter(ComponentFormatter[SandboxPruneResult, SandboxPruneView]):
    """Formatter for sandbox prune command results."""

    def transform(self, data: SandboxPruneResult) -> SandboxPruneView:
        """Derive the presentation-ready view from SandboxPruneResult.

        Args:
            data: Domain SandboxPruneResult instance.

        Returns:
            SandboxPruneView with transformed items and aggregate counts.
        """
        item_formatter = PrunedItemFormatter()
        items = [
            item_formatter.transform(item).model_copy(update={"is_dry_run": True})
            if data.dry_run
            else item_formatter.transform(item)
            for item in data.items
        ]

        return SandboxPruneView(
            status=data.status,
            dry_run=data.dry_run,
            force=data.force,
            items=items,
            pruned_count=data.pruned_count,
            skipped_count=data.skipped_count,
            failed_count=data.failed_count,
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_rich(self, data: SandboxPruneResult) -> Any:
        """Render sandbox prune action lines, empty state, or errors."""
        view = self.transform(data)
        return _format_prune_rich(view)
