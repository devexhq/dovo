"""ComponentFormatter for PrunedItem."""

from __future__ import annotations

from rich.text import Text

from dovo.cli.ui.formatters.sandbox.common import CATEGORY_LABELS
from dovo.cli.ui.formatters.sandbox.sandbox_views import PrunedItemView
from dovo.common.types import ComponentFormatter
from dovo.core.sandbox.models import PruneAction, PrunedItem


class PrunedItemFormatter(ComponentFormatter[PrunedItem, PrunedItemView]):
    """Formatter for single pruned resource items."""

    def transform(self, data: PrunedItem) -> PrunedItemView:
        """Derive the presentation-ready view from PrunedItem domain data."""
        is_dry_run = bool(data.reason and ("would prune" in data.reason.lower() or "dry run" in data.reason.lower()))
        category_label = CATEGORY_LABELS.get(data.category, str(data.category.value))

        return PrunedItemView(
            category=data.category,
            category_label=category_label,
            identifier=data.identifier,
            action=data.action,
            is_dry_run=is_dry_run,
            path=data.path,
            branch_name=data.branch_name,
            session_id=data.session_id,
            reason=data.reason,
            error=data.error,
        )

    def render_item(self, view: PrunedItemView) -> Text:
        """Render a styled action line from a PrunedItemView."""
        if view.action == PruneAction.PRUNED:
            if view.is_dry_run:
                verb = "Would prune"
                style = self._STYLE_MAP.get("warning", "yellow")
            else:
                verb = "Pruned"
                style = self._STYLE_MAP.get("success", "green")
        elif view.action == PruneAction.SKIPPED:
            verb = "Skipped"
            style = self._STYLE_MAP.get("warning", "yellow")
        else:
            verb = "Failed to prune"
            style = self._STYLE_MAP.get("error", "red")

        content = f"• {verb} {view.category_label}: {view.identifier}"
        if view.error:
            content += f" ({view.error})"

        return Text(content, style=style)

    def to_rich(self, data: PrunedItem) -> Text:
        """Render a styled action line for a pruned resource."""
        view = self.transform(data)
        return self.render_item(view)
