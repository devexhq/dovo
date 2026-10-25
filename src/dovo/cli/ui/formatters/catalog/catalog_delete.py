"""ComponentFormatter for CatalogDeleteResult."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.core.catalog.models import CatalogDeleteResult


class CatalogDeleteFormatter(ComponentFormatter[CatalogDeleteResult]):
    """Formatter for catalog delete command results."""

    def to_rich(self, data: CatalogDeleteResult) -> RenderableType:
        """Render single-line deletion confirmation, cancellation, or error panel."""
        if data.cancelled:
            return Text("Deletion cancelled.")

        if data.errors or not data.ok:
            return build_error_panel(
                "Catalog Delete Failed",
                data.errors,
                "Catalog delete failed.",
                remediations=data.remediations,
            )

        if data.deleted and data.item is not None:
            return Text(f"Deleted catalog blueprint '{data.item.sha}' ({data.item.path}).")

        return Text("")
