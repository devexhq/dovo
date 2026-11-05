from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from dovo.cli.ui.formatters.header import ResultHeaderView
from dovo.core.catalog.models import CatalogItemType, CatalogRecord


class CatalogItemView(BaseModel):
    """Semantic view of an indexed blueprint record."""

    model_config = {"extra": "forbid", "strict": True}

    sha: str
    item_type: str
    name: str
    path: str
    checksum: str
    tier: str


class CatalogTemplateView(BaseModel):
    """Semantic view of a packaged starter template."""

    model_config = {"extra": "forbid", "strict": True}

    item_type: str
    path: str


class CatalogListView(ResultHeaderView):
    """Semantic view of catalog blueprint and template listings."""

    item_type: str
    items: list[CatalogItemView] = Field(default_factory=list)
    type_filter: str | None = None
    templates: list[CatalogTemplateView] = Field(default_factory=list)
    total_items: int = 0


class CatalogShowView(ResultHeaderView):
    """Semantic view of catalog show results."""

    item_type: str
    item: CatalogItemView | None = None
    content: str | None = None
    template_matches: list[CatalogTemplateView] = Field(default_factory=list)
    catalog_path_relative: str | None = None


class CatalogValidateView(ResultHeaderView):
    """Semantic view of catalog validate results."""

    valid: bool
    status_label: str
    target: str
    resolved_path: str | None = None
    item_type: str


class CatalogCreateView(ResultHeaderView):
    """Semantic view of catalog create results: the header, then the created item and its resolved path."""

    item_type: CatalogItemType
    item: CatalogRecord | None = None
    resolved_path: Path | None = None


class CatalogDeleteView(ResultHeaderView):
    """Semantic view of catalog delete results: the header, then the item and what the delete did."""

    item_type: CatalogItemType
    item: CatalogRecord | None = None
    deleted: bool = False
    cancelled: bool = False
