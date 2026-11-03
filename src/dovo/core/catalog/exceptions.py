"""The catalog package's one domain error and the findings a failed definition check carries."""

from __future__ import annotations

from dataclasses import dataclass

from dovo.common.exceptions import DomainError, FailureData


class CatalogError(DomainError):
    """Business failure resolving, reading, scanning, creating, deleting or validating a catalog item."""


@dataclass(frozen=True, slots=True)
class CatalogValidateFindings(FailureData):
    """Every error message a failed definition check found, and the warnings found alongside them."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]
