"""Exceptions for the catalog inventory entrypoint and authored definitions."""

from __future__ import annotations

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import (
    DefinitionLoadError,
    DefinitionNotFoundError,
    DefinitionValidationError,
    DomainError,
)


class CatalogError(DomainError):
    """Business failure resolving, scanning, creating or deleting a catalog item."""


class CatalogFileNotFoundError(CatalogError):
    """Raised by Catalog.read_yaml when the path does not exist."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CATALOG_NOT_FOUND, message)


class CatalogYamlError(CatalogError):
    """Raised when catalog YAML is unreadable or not an object."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CATALOG_SYNTAX_ERROR, message)


class CatalogWriteError(CatalogError):
    """Raised when writing or reindexing a catalog file fails."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CATALOG_WRITE_FAILED, message)


class CatalogProtectionError(CatalogError):
    """Raised when attempting to delete or mutate a protected bundled catalog template."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CATALOG_PROTECTED, message)


class CatalogTierDeleteError(CatalogError):
    """Raised when attempting to delete a catalog item resolved from a non-REPO tier."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN, message)


class BlueprintLoadError(DefinitionLoadError):
    """Raised when blueprint YAML syntax is invalid or unreadable."""


class BlueprintValidationError(DefinitionValidationError):
    """Raised when blueprint model validation fails."""


class StepNotFoundError(DefinitionNotFoundError):
    """Raised when a step definition file or ID cannot be found."""


class StepValidationError(DefinitionValidationError):
    """Raised when step definition YAML parsing or schema validation fails."""


class AskStepInvalidError(DefinitionValidationError, ValueError):
    """Raised by step validation when a `type: ask` step breaks an authoring rule; subclasses ValueError so Pydantic reports it with this cause attached."""
