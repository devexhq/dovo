"""Shared exception hierarchy for catalog-backed domain definitions."""

from __future__ import annotations


class DefinitionError(Exception):
    """Base class for domain definition errors."""


class DefinitionNotFoundError(DefinitionError):
    """Raised when a definition file or identifier cannot be found."""


class DefinitionLoadError(DefinitionError):
    """Raised when definition YAML syntax is invalid or cannot be parsed/loaded."""


class DefinitionValidationError(DefinitionError):
    """Raised when definition schema or model validation fails."""
