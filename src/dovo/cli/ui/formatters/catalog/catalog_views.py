from __future__ import annotations

from pydantic import BaseModel, Field

from dovo.cli.ui.formatters.base import ResultView


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


class CatalogListView(ResultView):
    """Semantic view of catalog blueprint and template listings."""

    item_type: str
    items: list[CatalogItemView] = Field(default_factory=list)
    type_filter: str | None = None
    templates: list[CatalogTemplateView] = Field(default_factory=list)
    total_items: int = 0


class CatalogShowView(ResultView):
    """Semantic view of catalog show results."""

    item_type: str
    item: CatalogItemView | None = None
    content: str | None = None
    template_matches: list[CatalogTemplateView] = Field(default_factory=list)
    catalog_path_relative: str | None = None


class CatalogValidateView(ResultView):
    """Semantic view of catalog validate results."""

    valid: bool
    status_label: str
    target: str
    resolved_path: str | None = None
    item_type: str
