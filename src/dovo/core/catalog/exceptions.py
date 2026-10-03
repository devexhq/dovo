"""Exceptions for the catalog inventory facade and authored definitions."""

from __future__ import annotations

from dovo.common.exceptions import (
    DefinitionError,
    DefinitionLoadError,
    DefinitionNotFoundError,
    DefinitionValidationError,
)


class CatalogError(DefinitionError):
    """Base catalog facade error."""


class CatalogFileNotFoundError(CatalogError):
    """Raised by Catalog.read_yaml when the path does not exist."""


class CatalogYamlError(CatalogError):
    """Raised by Catalog.read_yaml when YAML is unreadable or not an object."""


class CatalogWriteError(CatalogError):
    """Raised by Catalog.save when the atomic write fails."""


class CatalogProtectionError(CatalogError):
    """Raised when attempting to delete or mutate a protected bundled catalog template."""


class CatalogTierDeleteError(CatalogError):
    """Raised when attempting to delete a catalog item resolved from a non-REPO tier."""


class BlueprintNotFoundError(DefinitionNotFoundError):
    """Raised when a blueprint name/SHA is not in the task/blueprint catalog."""


class BlueprintLoadError(DefinitionLoadError):
    """Raised when blueprint YAML syntax is invalid or unreadable."""


class BlueprintValidationError(DefinitionValidationError):
    """Raised when blueprint model validation fails."""


class StepNotFoundError(DefinitionNotFoundError):
    """Raised when a step definition file or ID cannot be found."""


class StepValidationError(DefinitionValidationError):
    """Raised when step definition YAML parsing or schema validation fails."""
