"""Tier 2 presentation contract tests for CatalogDeleteFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.catalog.catalog_delete import CatalogDeleteFormatter
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.catalog.models import (
    CatalogDeleteResult,
    CatalogItemType,
    CatalogRecord,
    CatalogTier,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = CatalogRecord(
    key="test-blueprint",
    sha="blueprint_1234567",
    item_type=CatalogItemType.BLUEPRINT,
    name="test-blueprint",
    namespace=None,
    path=Path("blueprints/test-blueprint.yml"),
    checksum="1234567890abcdef",
    tier=CatalogTier.REPO,
)

DELETED = FormatterCase.identity(
    data=CatalogDeleteResult(item=_RECORD, deleted=True, cancelled=False),
    render_expectations=[_RECORD.sha, str(_RECORD.path)],
)

CANCELLED = FormatterCase.identity(
    data=CatalogDeleteResult(cancelled=True),
    render_expectations=["Deletion cancelled."],
)

DELETE_ERROR = FormatterCase.identity(
    data=CatalogDeleteResult(
        error_code=ErrorCode.CATALOG_NOT_FOUND,
        item=None,
        deleted=False,
        cancelled=False,
        errors=["Catalog blueprint 'missing' not found."],
        remediations=[
            Remediation(message="Run 'dovo blueprint list' to inspect available items", command="dovo blueprint list")
        ],
    ),
    render_expectations=[
        "Catalog blueprint 'missing' not found.",
        "Fix:",
        "- Run 'dovo blueprint list' to inspect available items",
    ],
)

CATALOG_DELETE_CASES = [
    pytest.param(DELETED, id="deleted"),
    pytest.param(CANCELLED, id="cancelled"),
    pytest.param(DELETE_ERROR, id="delete_error"),
]

CATALOG_DELETE_PAYLOAD_CASES = [
    pytest.param(
        DELETED,
        {
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "error_code": None,
            "item": {
                "key": "test-blueprint",
                "sha": "blueprint_1234567",
                "item_type": "blueprint",
                "name": "test-blueprint",
                "namespace": None,
                "path": "blueprints/test-blueprint.yml",
                "checksum": "1234567890abcdef",
                "tier": "repo",
            },
            "deleted": True,
            "cancelled": False,
        },
        id="deleted",
    ),
    pytest.param(
        DELETE_ERROR,
        {
            "errors": ["Catalog blueprint 'missing' not found."],
            "warnings": [],
            "remediations": [
                {"message": "Run 'dovo blueprint list' to inspect available items", "command": "dovo blueprint list"}
            ],
            "diagnostics": [],
            "error_code": "CATALOG_NOT_FOUND",
            "item": None,
            "deleted": False,
            "cancelled": False,
        },
        id="delete_error",
    ),
    pytest.param(
        CANCELLED,
        {
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "error_code": None,
            "item": None,
            "deleted": False,
            "cancelled": True,
        },
        id="cancelled",
    ),
]


class CatalogDeleteFormatterTests:
    """Tier 2 presentation contract tests for CatalogDeleteFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), CATALOG_DELETE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[CatalogDeleteResult, CatalogDeleteResult],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(CatalogDeleteFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", CATALOG_DELETE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[CatalogDeleteResult, CatalogDeleteResult]
    ) -> None:
        assert_rich_render_shows_every_view_value(CatalogDeleteFormatter, case.data, case.render_expectations)
