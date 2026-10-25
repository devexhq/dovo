"""Tier 2 presentation contract tests for CatalogValidateFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.catalog.catalog_validate import CatalogValidateFormatter
from dovo.cli.ui.formatters.catalog.catalog_views import CatalogValidateView
from dovo.common.models import Remediation
from dovo.core.catalog.models import CatalogValidateResult, CatalogValidateStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

VALID_CASE = FormatterCase(
    data=CatalogValidateResult(
        status=CatalogValidateStatus.OK,
        valid=True,
        target="my-flow",
        resolved_path=Path(".dovo/catalog/blueprints/my-flow.yml"),
        item_type="blueprint",
        errors=[],
        warnings=[],
        remediations=[],
    ),
    view=CatalogValidateView(
        status="ok",
        valid=True,
        status_label="PASSED",
        target="my-flow",
        resolved_path=".dovo/catalog/blueprints/my-flow.yml",
        item_type="blueprint",
        errors=[],
        warnings=[],
        remediations=[],
    ),
    render_expectations=["PASSED", "my-flow", "blueprint", "blueprints/my-flow.yml"],
)

INVALID_CASE = FormatterCase(
    data=CatalogValidateResult(
        status=CatalogValidateStatus.INVALID,
        valid=False,
        target="dup.yml",
        resolved_path=Path("/workspace/dup.yml"),
        item_type="blueprint",
        errors=["Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."],
        warnings=["Step 's1' script_path 'scripts/none.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."],
        remediations=[
            Remediation(
                message="Resolve: Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."
            )
        ],
    ),
    view=CatalogValidateView(
        status="invalid",
        valid=False,
        status_label="FAILED",
        target="dup.yml",
        resolved_path="/workspace/dup.yml",
        item_type="blueprint",
        errors=["Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."],
        warnings=["Step 's1' script_path 'scripts/none.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."],
        remediations=[
            Remediation(
                message="Resolve: Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."
            )
        ],
    ),
    render_expectations=[
        "FAILED",
        "dup.yml",
        "CATALOG_DUPLICATE_STEP_ID",
        "run-tests",
        "CATALOG_SCRIPT_NOT_FOUND",
    ],
)

MORE_ERRORS_THAN_REMEDIATIONS_CASE = FormatterCase(
    data=CatalogValidateResult(
        status=CatalogValidateStatus.INVALID,
        valid=False,
        target="two.yml",
        resolved_path=Path("/workspace/two.yml"),
        item_type="blueprint",
        errors=[
            "Step id 'build' is used by more than one step (CATALOG_DUPLICATE_STEP_ID).",
            "Alias '-f' is declared by both 'foo' and 'bar' (CATALOG_DUPLICATE_INPUT_ALIAS).",
        ],
        remediations=[Remediation(message="Rename one of the 'build' steps")],
    ),
    view=CatalogValidateView(
        status="invalid",
        valid=False,
        status_label="FAILED",
        target="two.yml",
        resolved_path="/workspace/two.yml",
        item_type="blueprint",
        errors=[
            "Step id 'build' is used by more than one step (CATALOG_DUPLICATE_STEP_ID).",
            "Alias '-f' is declared by both 'foo' and 'bar' (CATALOG_DUPLICATE_INPUT_ALIAS).",
        ],
        remediations=[Remediation(message="Rename one of the 'build' steps")],
    ),
    render_expectations=[
        "FAILED",
        "CATALOG_DUPLICATE_STEP_ID",
        "CATALOG_DUPLICATE_INPUT_ALIAS",
        "Rename one of the 'build' steps",
    ],
)

VALIDATE_CASES = [
    pytest.param(VALID_CASE, id="valid"),
    pytest.param(INVALID_CASE, id="invalid"),
    pytest.param(MORE_ERRORS_THAN_REMEDIATIONS_CASE, id="more-errors-than-remediations"),
]

PAYLOAD_CASES = [
    pytest.param(
        VALID_CASE,
        {
            "status": "ok",
            "valid": True,
            "status_label": "PASSED",
            "target": "my-flow",
            "resolved_path": ".dovo/catalog/blueprints/my-flow.yml",
            "item_type": "blueprint",
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
        },
        id="valid_payload",
    ),
    pytest.param(
        INVALID_CASE,
        {
            "status": "invalid",
            "valid": False,
            "status_label": "FAILED",
            "target": "dup.yml",
            "resolved_path": "/workspace/dup.yml",
            "item_type": "blueprint",
            "error_code": None,
            "errors": ["Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID)."],
            "warnings": ["Step 's1' script_path 'scripts/none.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."],
            "remediations": [
                {
                    "message": "Resolve: Step id 'run-tests' is used by more than one step (CATALOG_DUPLICATE_STEP_ID).",
                    "command": None,
                }
            ],
        },
        id="invalid_payload",
    ),
]


class CatalogValidateFormatterTests:
    """Tier 2 presentation contract tests for CatalogValidateFormatter."""

    @pytest.mark.parametrize("case", VALIDATE_CASES)
    def test_transform_derives_flat_view_from_result(
        self, case: FormatterCase[CatalogValidateResult, CatalogValidateView]
    ) -> None:
        assert_transform_derives_expected_view(CatalogValidateFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[CatalogValidateResult, CatalogValidateView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(CatalogValidateFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", VALIDATE_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[CatalogValidateResult, CatalogValidateView]
    ) -> None:
        assert_rich_render_shows_every_view_value(CatalogValidateFormatter, case.data, case.render_expectations)
