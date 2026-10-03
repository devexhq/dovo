"""Tier 2 presentation contract tests for CatalogCreateFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.catalog.catalog_create import CatalogCreateFormatter
from dovo.core.catalog.models import CatalogCreateResult, CatalogItemType, CatalogRecord, CatalogTier
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
_REPO_RESOLVED_PATH = Path("/repo/.dovo/catalog/blueprints/test-blueprint.yml")

_USER_RECORD = CatalogRecord(
    key="user-blueprint",
    sha="blueprint_89abcde",
    item_type=CatalogItemType.BLUEPRINT,
    name="user-blueprint",
    namespace=None,
    path=Path("blueprints/user-blueprint.yml"),
    checksum="89abcdef01234567",
    tier=CatalogTier.USER,
)
_USER_RESOLVED_PATH = Path("/home/user/.dovo/user/catalog/blueprints/user-blueprint.yml")

CREATED_BLUEPRINT = FormatterCase(
    data=CatalogCreateResult(item=_RECORD, resolved_path=_REPO_RESOLVED_PATH),
    view=CatalogCreateResult(item=_RECORD, resolved_path=_REPO_RESOLVED_PATH),
    render_expectations=["blueprint", "test-blueprint", "repo", ".dovo/catalog/blueprints/test-blueprint.yml"],
)

CREATED_USER_BLUEPRINT = FormatterCase(
    data=CatalogCreateResult(item=_USER_RECORD, resolved_path=_USER_RESOLVED_PATH),
    view=CatalogCreateResult(item=_USER_RECORD, resolved_path=_USER_RESOLVED_PATH),
    render_expectations=["blueprint", "user-blueprint", "user", str(_USER_RESOLVED_PATH)],
)

CREATION_ERROR = FormatterCase(
    data=CatalogCreateResult(
        item=None,
        errors=["Naming collision on blueprint."],
        fixes=["Choose a different name"],
    ),
    view=CatalogCreateResult(
        item=None,
        errors=["Naming collision on blueprint."],
        fixes=["Choose a different name"],
    ),
    render_expectations=["Naming collision on blueprint.", "Choose a different name"],
)

CATALOG_CREATE_CASES = [
    pytest.param(CREATED_BLUEPRINT, id="created_blueprint"),
    pytest.param(CREATED_USER_BLUEPRINT, id="created_user_blueprint"),
    pytest.param(CREATION_ERROR, id="creation_error"),
]

CATALOG_CREATE_PAYLOAD_CASES = [
    pytest.param(
        CREATED_BLUEPRINT,
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
            "resolved_path": str(_REPO_RESOLVED_PATH),
        },
        id="created_blueprint",
    ),
    pytest.param(
        CREATED_USER_BLUEPRINT,
        {
            "errors": [],
            "warnings": [],
            "fixes": [],
            "error_code": None,
            "item": {
                "key": "user-blueprint",
                "sha": "blueprint_89abcde",
                "item_type": "blueprint",
                "name": "user-blueprint",
                "namespace": None,
                "path": "blueprints/user-blueprint.yml",
                "checksum": "89abcdef01234567",
                "tier": "user",
            },
            "resolved_path": str(_USER_RESOLVED_PATH),
        },
        id="created_user_blueprint",
    ),
    pytest.param(
        CREATION_ERROR,
        {
            "errors": ["Naming collision on blueprint."],
            "warnings": [],
            "fixes": ["Choose a different name"],
            "error_code": None,
            "item": None,
            "resolved_path": None,
        },
        id="creation_error",
    ),
]


class CatalogCreateFormatterTests:
    """Tier 2 presentation contract tests for CatalogCreateFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), CATALOG_CREATE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[CatalogCreateResult, CatalogCreateResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(CatalogCreateFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", CATALOG_CREATE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[CatalogCreateResult, CatalogCreateResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(CatalogCreateFormatter, case.data, case.render_expectations)
