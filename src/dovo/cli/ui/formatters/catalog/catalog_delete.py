"""ComponentFormatter for CatalogDeleteResult."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.catalog.catalog_views import CatalogDeleteView
from dovo.cli.ui.formatters.catalog.common import CatalogFormatter
from dovo.cli.ui.formatters.common import build_error_panel, with_diagnostics
from dovo.cli.ui.formatters.header import header_fields
from dovo.common.utils import enum_value
from dovo.core.catalog.models import CatalogDeleteResult, CatalogItemType


def _render(view: CatalogDeleteView) -> RenderableType:
    """Render single-line deletion confirmation, cancellation, or error panel."""
    if view.cancelled:
        return Text("Deletion cancelled.")

    if view.errors or not view.ok:
        return build_error_panel(
            "Catalog Delete Failed",
            view.errors,
            "Catalog delete failed.",
            remediations=view.remediations,
        )

    if view.deleted and view.item is not None:
        return Text(f"Deleted catalog {enum_value(view.item_type)} '{view.item.sha}' ({view.item.path}).")

    return Text("")


class CatalogDeleteFormatter(CatalogFormatter[CatalogDeleteResult, CatalogDeleteView]):
    """Formatter for catalog delete command results."""

    event_types: ClassVar[Mapping[CatalogItemType, str]] = {
        CatalogItemType.BLUEPRINT: "blueprint.delete",
        CatalogItemType.STEP: "step.delete",
    }

    def transform(self, data: CatalogDeleteResult) -> CatalogDeleteView:
        """Derive the view: the result header, then the item and what the delete did."""
        return CatalogDeleteView(
            **header_fields(data),
            item_type=data.item_type,
            item=data.item,
            deleted=data.deleted,
            cancelled=data.cancelled,
        )

    def to_rich(self, data: CatalogDeleteResult) -> RenderableType:
        """Render single-line deletion confirmation, cancellation, or error panel, followed by diagnostic_lines(diagnostics)."""
        view = self.transform(data)
        return with_diagnostics(_render(view), view.diagnostics)
