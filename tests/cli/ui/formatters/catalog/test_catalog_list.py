"""Tier 2 presentation contract tests for CatalogListFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.catalog.catalog_list import CatalogListFormatter
from dovo.cli.ui.formatters.catalog.catalog_views import (
    CatalogItemView,
    CatalogListView,
    CatalogTemplateView,
)
from dovo.core.catalog.models import CatalogItemType, CatalogListResult, CatalogRecord, CatalogTier
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _sample_catalog_record() -> CatalogRecord:
    return CatalogRecord(
        key="test-blueprint",
        sha="blueprint_1234567",
        item_type=CatalogItemType.BLUEPRINT,
        name="test-blueprint",
        namespace=None,
        path=Path("blueprints/test-blueprint.yml"),
        checksum="1234567890abcdef",
        tier=CatalogTier.REPO,
    )


def _sample_step_record() -> CatalogRecord:
    return CatalogRecord(
        key="test-step",
        sha="step_1234567",
        item_type=CatalogItemType.STEP,
        name="test-step",
        namespace=None,
        path=Path("steps/test-step.yml"),
        checksum="abcdef1234567890",
        tier=CatalogTier.REPO,
    )


WITH_ITEMS = FormatterCase(
    data=CatalogListResult(items=[_sample_catalog_record()]),
    view=CatalogListView(
        items=[
            CatalogItemView(
                sha="blueprint_1234567",
                item_type="blueprint",
                name="test-blueprint",
                path="blueprints/test-blueprint.yml",
                checksum="1234567890abcdef",
                tier="repo",
            )
        ],
        type_filter=None,
        templates=[],
        total_items=1,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["test-blueprint", "blueprint_1234567", "blueprint", "repo", "blueprints/test-blueprint.yml"],
)

EMPTY_ITEMS = FormatterCase(
    data=CatalogListResult(items=[]),
    view=CatalogListView(
        items=[],
        type_filter=None,
        templates=[],
        total_items=0,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=[],
)

WITH_STEP_ITEMS = FormatterCase(
    data=CatalogListResult(items=[_sample_step_record()], type_filter=CatalogItemType.STEP),
    view=CatalogListView(
        items=[
            CatalogItemView(
                sha="step_1234567",
                item_type="step",
                name="test-step",
                path="steps/test-step.yml",
                checksum="abcdef1234567890",
                tier="repo",
            )
        ],
        type_filter="step",
        templates=[],
        total_items=1,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["test-step", "step_1234567", "steps/test-step.yml", "Steps:"],
)

EMPTY_STEP_ITEMS = FormatterCase(
    data=CatalogListResult(items=[], type_filter=CatalogItemType.STEP),
    view=CatalogListView(
        items=[],
        type_filter="step",
        templates=[],
        total_items=0,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["No steps found."],
)

TEMPLATES = FormatterCase(
    data=CatalogListResult(templates=[("blueprint", "blueprints/default.yml")]),
    view=CatalogListView(
        items=[],
        type_filter=None,
        templates=[CatalogTemplateView(item_type="blueprint", path="blueprints/default.yml")],
        total_items=0,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["blueprint", "blueprints/default.yml"],
)

EMPTY_TEMPLATES = FormatterCase(
    data=CatalogListResult(type_filter="template", templates=[]),
    view=CatalogListView(
        items=[],
        type_filter="template",
        templates=[],
        total_items=0,
        errors=[],
        warnings=[],
        fixes=[],
    ),
    render_expectations=[],
)

WITH_ERRORS = FormatterCase(
    data=CatalogListResult(errors=["Invalid --type argument 'invalid'."]),
    view=CatalogListView(
        items=[],
        type_filter=None,
        templates=[],
        total_items=0,
        errors=["Invalid --type argument 'invalid'."],
        warnings=[],
        fixes=[],
    ),
    render_expectations=["Invalid --type argument 'invalid'."],
)

WITH_WARNINGS = FormatterCase(
    data=CatalogListResult(items=[_sample_catalog_record()], warnings=["Failed to parse corrupted.yml"]),
    view=CatalogListView(
        items=[
            CatalogItemView(
                sha="blueprint_1234567",
                item_type="blueprint",
                name="test-blueprint",
                path="blueprints/test-blueprint.yml",
                checksum="1234567890abcdef",
                tier="repo",
            )
        ],
        type_filter=None,
        templates=[],
        total_items=1,
        errors=[],
        warnings=["Failed to parse corrupted.yml"],
        fixes=[],
    ),
    render_expectations=[
        "test-blueprint",
        "blueprint_1234567",
        "blueprint",
        "repo",
        "blueprints/test-blueprint.yml",
        "Failed to parse corrupted.yml",
    ],
)

LIST_CASES = [
    pytest.param(WITH_ITEMS, id="with_items"),
    pytest.param(EMPTY_ITEMS, id="empty_items"),
    pytest.param(WITH_STEP_ITEMS, id="with_step_items"),
    pytest.param(EMPTY_STEP_ITEMS, id="empty_step_items"),
    pytest.param(TEMPLATES, id="templates"),
    pytest.param(EMPTY_TEMPLATES, id="empty_templates"),
    pytest.param(WITH_ERRORS, id="with_errors"),
    pytest.param(WITH_WARNINGS, id="with_warnings"),
]

PAYLOAD_CASES = [
    pytest.param(
        WITH_ITEMS,
        {
            "errors": [],
            "warnings": [],
            "fixes": [],
            "items": [
                {
                    "sha": "blueprint_1234567",
                    "item_type": "blueprint",
                    "name": "test-blueprint",
                    "path": "blueprints/test-blueprint.yml",
                    "checksum": "1234567890abcdef",
                    "tier": "repo",
                }
            ],
            "type_filter": None,
            "templates": [],
            "total_items": 1,
        },
        id="with_items_payload",
    ),
    pytest.param(
        EMPTY_ITEMS,
        {
            "errors": [],
            "warnings": [],
            "fixes": [],
            "items": [],
            "type_filter": None,
            "templates": [],
            "total_items": 0,
        },
        id="empty_items_payload",
    ),
    pytest.param(
        TEMPLATES,
        {
            "errors": [],
            "warnings": [],
            "fixes": [],
            "items": [],
            "type_filter": None,
            "templates": [{"item_type": "blueprint", "path": "blueprints/default.yml"}],
            "total_items": 0,
        },
        id="templates_payload",
    ),
    pytest.param(
        WITH_ERRORS,
        {
            "errors": ["Invalid --type argument 'invalid'."],
            "warnings": [],
            "fixes": [],
            "items": [],
            "type_filter": None,
            "templates": [],
            "total_items": 0,
        },
        id="with_errors_payload",
    ),
    pytest.param(
        WITH_WARNINGS,
        {
            "errors": [],
            "warnings": ["Failed to parse corrupted.yml"],
            "fixes": [],
            "items": [
                {
                    "sha": "blueprint_1234567",
                    "item_type": "blueprint",
                    "name": "test-blueprint",
                    "path": "blueprints/test-blueprint.yml",
                    "checksum": "1234567890abcdef",
                    "tier": "repo",
                }
            ],
            "type_filter": None,
            "templates": [],
            "total_items": 1,
        },
        id="with_warnings_payload",
    ),
]


class CatalogListFormatterTests:
    """Tier 2 presentation contract tests for CatalogListFormatter."""

    @pytest.mark.parametrize("case", LIST_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[CatalogListResult, CatalogListView]) -> None:
        """Verify transform derives the expected CatalogListView."""
        assert_transform_derives_expected_view(CatalogListFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[CatalogListResult, CatalogListView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(CatalogListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", LIST_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[CatalogListResult, CatalogListView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(CatalogListFormatter, case.data, case.render_expectations)
