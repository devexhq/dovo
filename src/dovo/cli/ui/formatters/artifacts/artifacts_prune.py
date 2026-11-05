"""ComponentFormatter for ArtifactsPruneResult."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactsPruneView
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.header import header_fields
from dovo.core.artifacts.models import ArtifactsPruneResult, PrunedArtifact


def _render_item(item: PrunedArtifact) -> Text:
    """Render one PrunedArtifact as a single line."""
    if item.pruned:
        return Text(f"Pruned: {item.session_id}/{item.name}@{item.revision}", style="green")
    return Text(f"Failed: {item.session_id}/{item.name}@{item.revision} ({item.error})", style="red")


class ArtifactsPruneFormatter(ComponentFormatter[ArtifactsPruneResult, ArtifactsPruneView]):
    """Formatter for `dovo artifacts prune` command results."""

    event_type: ClassVar[str] = "artifacts.prune"

    def transform(self, data: ArtifactsPruneResult) -> ArtifactsPruneView:
        """Derive the presentation-ready view from ArtifactsPruneResult, including pruned/failed counts."""
        return ArtifactsPruneView(
            **header_fields(data),
            disabled=data.disabled,
            dry_run=data.dry_run,
            force=data.force,
            items=list(data.items),
            pruned_count=sum(1 for item in data.items if item.pruned),
            failed_count=sum(1 for item in data.items if not item.pruned),
        )

    def to_rich(self, data: ArtifactsPruneResult) -> RenderableType:
        """Render prune action lines, disabled state, or empty state."""
        view = self.transform(data)
        if view.disabled:
            return Text("Artifact pruning is disabled; pass --force to override.")
        if not view.items:
            return Text("No expired artifacts found.")
        return Group(*(_render_item(item) for item in view.items))
