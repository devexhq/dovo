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


def _record(name: str, item_type: CatalogItemType) -> CatalogRecord:
    return CatalogRecord(
        key=name,
        sha=f"{item_type.value}_1234567",
        item_type=item_type,
        name=name,
        namespace=None,
        path=Path(f"{item_type.value}s/{name}.yml"),
        checksum="1234567890abcdef",
        tier=CatalogTier.REPO,
    )


def _item_view(name: str, item_type: str) -> CatalogItemView:
    return CatalogItemView(
        sha=f"{item_type}_1234567",
        item_type=item_type,
        name=name,
        path=f"{item_type}s/{name}.yml",
        checksum="1234567890abcdef",
        tier="repo",
    )


_EMPTY_VIEW = CatalogListView(
    item_type="blueprint",
    items=[],
    type_filter="blueprint",
    templates=[],
    total_items=0,
    errors=[],
    warnings=[],
    remediations=[],
)
_BP = CatalogItemType.BLUEPRINT


WITH_ITEMS = FormatterCase(
    data=CatalogListResult(
        item_type=_BP, type_filter=_BP, items=[_record("test-blueprint", CatalogItemType.BLUEPRINT)]
    ),
    view=_EMPTY_VIEW.model_copy(update={"items": [_item_view("test-blueprint", "blueprint")], "total_items": 1}),
    render_expectations=["test-blueprint", "blueprint_1234567", "blueprint", "repo", "blueprints/test-blueprint.yml"],
)

EMPTY_ITEMS = FormatterCase(
    data=CatalogListResult(item_type=_BP, type_filter=_BP, items=[]),
    view=_EMPTY_VIEW,
    render_expectations=["No blueprints found."],
)

WITH_STEP_ITEMS = FormatterCase(
    data=CatalogListResult(
        item_type=CatalogItemType.STEP,
        items=[_record("test-step", CatalogItemType.STEP)],
        type_filter=CatalogItemType.STEP,
    ),
    view=_EMPTY_VIEW.model_copy(
        update={
            "item_type": "step",
            "items": [_item_view("test-step", "step")],
            "type_filter": "step",
            "total_items": 1,
        }
    ),
    render_expectations=["Steps:", "test-step", "step_1234567", "steps/test-step.yml"],
)

EMPTY_STEP_ITEMS = FormatterCase(
    data=CatalogListResult(item_type=CatalogItemType.STEP, items=[], type_filter=CatalogItemType.STEP),
    view=_EMPTY_VIEW.model_copy(update={"item_type": "step", "type_filter": "step"}),
    render_expectations=["No steps found."],
)

TEMPLATES = FormatterCase(
    data=CatalogListResult(item_type=_BP, type_filter=_BP, templates=[("blueprint", "blueprints/default.yml")]),
    view=_EMPTY_VIEW.model_copy(
        update={"templates": [CatalogTemplateView(item_type="blueprint", path="blueprints/default.yml")]}
    ),
    render_expectations=["blueprint", "blueprints/default.yml"],
)

WITH_ERRORS = FormatterCase(
    data=CatalogListResult(item_type=_BP, error_code="CATALOG_WRITE_FAILED", errors=["Failed to write catalog index."]),
    view=_EMPTY_VIEW.model_copy(update={"type_filter": None, "errors": ["Failed to write catalog index."]}),
    render_expectations=["Failed to write catalog index."],
)

WITH_WARNINGS = FormatterCase(
    data=WITH_ITEMS.data.model_copy(update={"warnings": ["Failed to parse corrupted.yml"]}),
    view=WITH_ITEMS.view.model_copy(update={"warnings": ["Failed to parse corrupted.yml"]}),
    render_expectations=[*WITH_ITEMS.render_expectations, "Failed to parse corrupted.yml"],
)

LIST_CASES = [
    pytest.param(WITH_ITEMS, id="with_items"),
    pytest.param(EMPTY_ITEMS, id="empty_items"),
    pytest.param(WITH_STEP_ITEMS, id="with_step_items"),
    pytest.param(EMPTY_STEP_ITEMS, id="empty_step_items"),
    pytest.param(TEMPLATES, id="templates"),
    pytest.param(WITH_ERRORS, id="with_errors"),
    pytest.param(WITH_WARNINGS, id="with_warnings"),
]

PAYLOAD_CASES = [
    pytest.param(
        WITH_WARNINGS,
        {
            "errors": [],
            "warnings": ["Failed to parse corrupted.yml"],
            "remediations": [],
            "diagnostics": [],
            "item_type": "blueprint",
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
            "type_filter": "blueprint",
            "templates": [],
            "total_items": 1,
        },
        id="populated_payload",
    ),
    pytest.param(
        EMPTY_ITEMS,
        {
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "item_type": "blueprint",
            "items": [],
            "type_filter": "blueprint",
            "templates": [],
            "total_items": 0,
        },
        id="empty_payload",
    ),
]


class CatalogListFormatterTests:
    """Tier 2 presentation contract tests for CatalogListFormatter."""

    @pytest.mark.parametrize("case", LIST_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[CatalogListResult, CatalogListView]) -> None:
        assert_transform_derives_expected_view(CatalogListFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[CatalogListResult, CatalogListView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(CatalogListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", LIST_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[CatalogListResult, CatalogListView]) -> None:
        assert_rich_render_shows_every_view_value(CatalogListFormatter, case.data, case.render_expectations)
