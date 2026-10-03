"""Models for catalog scanner and blueprint management."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from dovo.common.models import BaseResult, DefinitionResolutionStatus


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


class SeedResult(BaseResult):
    """Outcome of seeding packaged catalog blueprint templates."""

    created_files: list[Path] = Field(default_factory=list)
    skipped_existing_files: list[Path] = Field(default_factory=list)
    overwritten_files: list[Path] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when seeding completed without blocking errors."""
        return not self.errors


class CatalogScanResult(BaseResult):
    """Result of scanning and indexing catalog blueprint directories."""

    items: list[CatalogRecord] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when scanning and rewriting index.json completed without errors."""
        return not self.errors


class CatalogSubdirectoryScanResult(BaseResult):
    """Result of scanning a catalog subdirectory."""

    scanned_records: list[CatalogIndexEntry] = Field(default_factory=list)
    scanned_shas: set[str] = Field(default_factory=set)


class YamlParseOutcome(BaseModel):
    """Outcome of reading and parsing a YAML catalog blueprint file."""

    model_config = {"extra": "forbid", "strict": True}

    parsed_data: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)


class DefinitionValidationOutcome(BaseModel):
    """Outcome of validating a catalog blueprint definition against schema and model."""

    model_config = {"extra": "forbid", "strict": True}

    definition: Any | None = None
    status: DefinitionResolutionStatus = DefinitionResolutionStatus.OK
    errors: list[str] = Field(default_factory=list)


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
    """Result of deleting a catalog blueprint."""

    item: CatalogRecord | None = None
    deleted: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """Return True if item deletion succeeded."""
        return not self.errors and self.deleted


class CatalogCreateResult(BaseResult):
    """Result of creating a catalog blueprint or step."""

    item: CatalogRecord | None = None
    resolved_path: Path | None = None

    @property
    def ok(self) -> bool:
        """Return True if blueprint creation succeeded."""
        return not self.errors and self.item is not None


class CatalogValidateStatus(StrEnum):
    """Classified outcomes for validating a catalog blueprint or step definition."""

    OK = "ok"
    INVALID = "invalid"
    SYNTAX_ERROR = "syntax_error"
    NOT_FOUND = "not_found"
    UNREADABLE = "unreadable"
    TYPE_REQUIRED = "type_required"


class CatalogValidateResult(BaseResult):
    """Non-raising result of validating one catalog blueprint or step definition."""

    status: CatalogValidateStatus
    valid: bool
    target: str
    resolved_path: Path | None = None
    item_type: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the item is valid and carries no errors."""
        return self.valid and not self.errors
