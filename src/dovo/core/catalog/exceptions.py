"""Exceptions for the catalog inventory entrypoint and authored definitions."""

from __future__ import annotations

from dataclasses import dataclass

from dovo.common.exceptions import (
    DefinitionLoadError,
    DefinitionNotFoundError,
    DefinitionValidationError,
    DomainError,
    FailureData,
)


class CatalogError(DomainError):
    """Business failure resolving, reading, scanning, creating, deleting or validating a catalog item."""


@dataclass(frozen=True, slots=True)
class CatalogValidateFindings(FailureData):
    """Every error message a failed definition check found, and the warnings found alongside them."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]


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
