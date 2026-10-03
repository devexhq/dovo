from __future__ import annotations

from pydantic import BaseModel, Field


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


class CatalogListView(BaseModel):
    """Semantic view of catalog blueprint and template listings."""

    model_config = {"extra": "forbid", "strict": True}

    items: list[CatalogItemView] = Field(default_factory=list)
    type_filter: str | None = None
    templates: list[CatalogTemplateView] = Field(default_factory=list)
    total_items: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class CatalogShowView(BaseModel):
    """Semantic view of catalog show results."""

    model_config = {"extra": "forbid", "strict": True}

    item: CatalogItemView | None = None
    content: str | None = None
    template_matches: list[CatalogTemplateView] = Field(default_factory=list)
    catalog_path_relative: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


class CatalogValidateView(BaseModel):
    """Semantic view of catalog validate results."""

    model_config = {"extra": "forbid", "strict": True}

    status: str
    valid: bool
    status_label: str
    target: str
    resolved_path: str | None = None
    item_type: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
