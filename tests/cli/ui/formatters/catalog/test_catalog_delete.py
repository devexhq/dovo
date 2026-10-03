"""Tier 2 presentation contract tests for CatalogDeleteFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.catalog.catalog_delete import CatalogDeleteFormatter
from dovo.core.catalog.models import CatalogDeleteResult, CatalogItemType, CatalogRecord, CatalogTier
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

DELETED = FormatterCase(
    data=CatalogDeleteResult(item=_RECORD, deleted=True, cancelled=False),
    view=CatalogDeleteResult(item=_RECORD, deleted=True, cancelled=False),
    render_expectations=[_RECORD.sha, str(_RECORD.path)],
)

CANCELLED = FormatterCase(
    data=CatalogDeleteResult(item=None, deleted=False, cancelled=True, errors=["Deletion cancelled."]),
    view=CatalogDeleteResult(item=None, deleted=False, cancelled=True, errors=["Deletion cancelled."]),
    render_expectations=["Deletion cancelled."],
)

DELETE_ERROR = FormatterCase(
    data=CatalogDeleteResult(
        item=None,
        deleted=False,
        cancelled=False,
        errors=["Catalog blueprint 'missing' not found."],
        fixes=["Run `dovo blueprint list` to inspect available items"],
    ),
    view=CatalogDeleteResult(
        item=None,
        deleted=False,
        cancelled=False,
        errors=["Catalog blueprint 'missing' not found."],
        fixes=["Run `dovo blueprint list` to inspect available items"],
    ),
    render_expectations=[
        "Catalog blueprint 'missing' not found.",
        "Run `dovo blueprint list` to inspect available items",
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
            "fixes": [],
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
        CANCELLED,
        {
            "errors": ["Deletion cancelled."],
            "warnings": [],
            "fixes": [],
            "error_code": None,
            "item": None,
            "deleted": False,
            "cancelled": True,
        },
        id="cancelled",
    ),
    pytest.param(
        DELETE_ERROR,
        {
            "errors": ["Catalog blueprint 'missing' not found."],
            "warnings": [],
            "fixes": ["Run `dovo blueprint list` to inspect available items"],
            "error_code": None,
            "item": None,
            "deleted": False,
            "cancelled": False,
        },
        id="delete_error",
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
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(CatalogDeleteFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", CATALOG_DELETE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[CatalogDeleteResult, CatalogDeleteResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(CatalogDeleteFormatter, case.data, case.render_expectations)
