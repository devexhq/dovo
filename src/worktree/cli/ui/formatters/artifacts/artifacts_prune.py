"""ComponentFormatter for ArtifactsPruneResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from worktree.cli.ui.formatters.artifacts.artifacts_views import ArtifactsPruneView
from worktree.common.types import ComponentFormatter
from worktree.core.artifacts.models import ArtifactsPruneResult, ArtifactsPruneStatus, PrunedArtifact


def _render_item(item: PrunedArtifact) -> Text:
    """Render one PrunedArtifact as a single line."""
    if item.pruned:
        return Text(f"Pruned: {item.session_id}/{item.name}", style="green")
    return Text(f"Failed: {item.session_id}/{item.name} ({item.error})", style="red")


class ArtifactsPruneFormatter(ComponentFormatter[ArtifactsPruneResult, ArtifactsPruneView]):
    """Formatter for `wt artifacts prune` command results."""

    def transform(self, data: ArtifactsPruneResult) -> ArtifactsPruneView:
        """Derive the presentation-ready view from ArtifactsPruneResult, including pruned/failed counts."""
        return ArtifactsPruneView(
            status=data.status,
            dry_run=data.dry_run,
            force=data.force,
            items=list(data.items),
            pruned_count=sum(1 for item in data.items if item.pruned),
            failed_count=sum(1 for item in data.items if not item.pruned),
        )

    def to_rich(self, data: ArtifactsPruneResult) -> Any:
        """Render prune action lines, disabled state, or empty state."""
        view = self.transform(data)
        if view.status == ArtifactsPruneStatus.DISABLED:
            return Text("Artifact pruning is disabled; pass --force to override.")
        if not view.items:
            return Text("No expired artifacts found.")
        return Group(*(_render_item(item) for item in view.items))
