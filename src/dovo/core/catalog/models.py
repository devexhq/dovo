"""Models for catalog scanner and blueprint management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from dovo.common.models import BaseResult


class CatalogTier(StrEnum):
    """Precedence tiers for multi-tier catalog resolution, matching ConfigTier's ordering."""

    PACKAGED = "packaged"
    GLOBAL = "global"
    USER = "user"
    REPO = "repo"


class CatalogItemType(StrEnum):
    """Supported catalog item classification types."""

    BLUEPRINT = "blueprint"
    STEP = "step"


class CatalogItemTypeDirectory(StrEnum):
    """Catalog item type directories."""

    BLUEPRINT = "blueprints"
    STEP = "steps"


class CatalogItem[T](BaseModel):
    """A validated definition paired with its catalog-relative location."""

    model_config = {"extra": "forbid", "strict": True}

    _ITEM_TYPES_BY_DIRECTORY: ClassVar[dict[str, CatalogItemType]] = {
        "blueprints": CatalogItemType.BLUEPRINT,
        "steps": CatalogItemType.STEP,
    }

    path: Path
    definition: T

    @field_validator("path")
    @classmethod
    def validate_catalog_relative_path(cls, path: Path) -> Path:
        """Require a relative YAML path rooted in a supported catalog directory."""
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Catalog item path must be relative and cannot contain '..' segments.")
        if len(path.parts) < 2 or path.parts[0] not in cls._ITEM_TYPES_BY_DIRECTORY:
            allowed_directories = ", ".join(sorted(cls._ITEM_TYPES_BY_DIRECTORY))
            raise ValueError(f"Catalog item path must start with one of: {allowed_directories}.")
        if path.suffix not in {".yml", ".yaml"} or path.stem == path.suffix:
            raise ValueError("Catalog item path must name a .yml or .yaml file.")
        return path

    @property
    def item_type(self) -> CatalogItemType:
        """Return the item type determined by the catalog root directory."""
        return self._ITEM_TYPES_BY_DIRECTORY[self.path.parts[0]]

    @property
    def file_stem(self) -> str:
        """Return the YAML filename stem."""
        return self.path.stem

    @property
    def namespace(self) -> str | None:
        """Return the nested catalog directory below the item-type root."""
        parts = self.path.parts[1:-1]
        return "/".join(parts) if parts else None

    @property
    def key(self) -> str:
        """Return the catalog lookup key derived from path and namespace."""
        return f"{self.namespace}/{self.file_stem}" if self.namespace else self.file_stem


class CatalogIndexEntry(BaseModel):
    """One catalog item's identity and disk location within a single tier's index.json."""

    model_config = {"extra": "forbid", "strict": True}

    sha: str
    key: str
    item_type: CatalogItemType
    name: str
    namespace: str | None = None
    path: Path
    checksum: str


class CatalogIndex(BaseModel):
    """On-disk contents of one tier root's index.json."""

    model_config = {"extra": "forbid", "strict": True}

    items: list[CatalogIndexEntry] = Field(default_factory=list)


class CatalogRecord(CatalogIndexEntry):
    """One resolved catalog item: its indexed identity plus the tier it was resolved from."""

    tier: CatalogTier


@dataclass(frozen=True, slots=True)
class CatalogScan:
    """Records indexed by a catalog scan, and the read errors of files it skipped."""

    records: tuple[CatalogRecord, ...]
    skipped_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogResolution:
    """The highest-precedence record matching a key, its checked definition when one was requested, and every match.

    warnings holds the duplicate-name warning when the winner's tier has more than one match, followed by the
    definition's validation warnings when a definition was requested.
    """

    record: CatalogRecord
    definition: object | None
    matches: tuple[CatalogRecord, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SeedSummary:
    """The packaged catalog template files seeding created, skipped as already present, or overwritten."""

    created_files: tuple[Path, ...]
    skipped_existing_files: tuple[Path, ...]
    overwritten_files: tuple[Path, ...]


class CatalogListResult(BaseResult):
    """Result of listing catalog blueprints and templates."""

    items: list[CatalogRecord] = Field(default_factory=list)
    type_filter: CatalogItemType | str | None = None
    templates: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True if list operation completed without errors."""
        return not self.errors


class CatalogShowResult(BaseResult):
    """Result of showing a catalog blueprint or packaged template."""

    item: CatalogRecord | None = None
    content: str | None = None
    template_matches: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True if a blueprint or template was found without errors."""
        return not self.errors and (self.item is not None or self.content is not None or bool(self.template_matches))


class CatalogDeleteResult(BaseResult):
    """Result of deleting a catalog blueprint or step."""

    item: CatalogRecord | None = None
    deleted: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """Return True unless the delete failed; a declined confirmation is ok."""
        return self.error_code is None


class CatalogCreateResult(BaseResult):
    """Result of creating a catalog blueprint or step."""

    item: CatalogRecord | None = None
    resolved_path: Path | None = None

    @property
    def ok(self) -> bool:
        """Return True unless the create failed."""
        return self.error_code is None


class CatalogValidateResult(BaseResult):
    """Non-raising result of validating one catalog blueprint or step definition."""

    valid: bool
    target: str
    resolved_path: Path | None = None
    item_type: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when validation found no failure; equals valid."""
        return self.error_code is None
