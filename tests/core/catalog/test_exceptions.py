"""Contract tests for the catalog package's domain error and its fixed-code subclasses."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError
from dovo.core.catalog.exceptions import (
    CatalogError,
    CatalogFileNotFoundError,
    CatalogProtectionError,
    CatalogTierDeleteError,
    CatalogWriteError,
    CatalogYamlError,
)


class CatalogErrorTests:
    @pytest.mark.parametrize(
        ("error_cls", "code"),
        [
            pytest.param(CatalogFileNotFoundError, ErrorCode.CATALOG_NOT_FOUND, id="file_not_found"),
            pytest.param(CatalogYamlError, ErrorCode.CATALOG_SYNTAX_ERROR, id="yaml"),
            pytest.param(CatalogWriteError, ErrorCode.CATALOG_WRITE_FAILED, id="write"),
            pytest.param(CatalogProtectionError, ErrorCode.CATALOG_PROTECTED, id="protection"),
            pytest.param(CatalogTierDeleteError, ErrorCode.CATALOG_TIER_DELETE_FORBIDDEN, id="tier_delete"),
        ],
    )
    def test_subclass_carries_fixed_code(self, error_cls: Callable[[str], CatalogError], code: ErrorCode) -> None:
        """Construct error_cls("m") with error_code == code, message == "m", remediations == (), and isinstance DomainError."""
        error = error_cls("m")

        assert (error.error_code, error.message, error.remediations) == (code, "m", ())
        assert isinstance(error, DomainError)
