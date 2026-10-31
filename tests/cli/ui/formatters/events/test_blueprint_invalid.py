"""Tier 2 presentation contract tests for BlueprintInvalidFormatter."""

from __future__ import annotations

from dovo.cli.ui.events import BlueprintInvalidEvent
from dovo.cli.ui.formatters.events.blueprint_invalid import BlueprintInvalidFormatter
from dovo.common.models import Remediation
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

DUPLICATE_STEP_ID = FormatterCase.identity(
    data=BlueprintInvalidEvent(
        blueprint="deploy",
        error_code="CATALOG_SCHEMA_INVALID",
        errors=["Step id 'a' is used by more than one step."],
        remediations=[Remediation(message="Resolve: Step id 'a' is used by more than one step.")],
    ),
    render_expectations=[
        "Blueprint Invalid",
        "Blueprint 'deploy' failed validation; no steps ran.",
        "- Step id 'a' is used by more than one step.",
        "- Resolve: Step id 'a' is used by more than one step.",
    ],
)


class BlueprintInvalidFormatterTests:
    """Tier 2 presentation contract tests for BlueprintInvalidFormatter."""

    def test_json_payload_matches_published_shape(self) -> None:
        assert_json_payload_matches_published_shape(
            BlueprintInvalidFormatter,
            DUPLICATE_STEP_ID.data,
            {
                "blueprint": "deploy",
                "error_code": "CATALOG_SCHEMA_INVALID",
                "errors": ["Step id 'a' is used by more than one step."],
                "remediations": [{"message": "Resolve: Step id 'a' is used by more than one step.", "command": None}],
            },
        )

    def test_rich_render_shows_headline_errors_and_fix(self) -> None:
        assert_rich_render_shows_every_view_value(
            BlueprintInvalidFormatter, DUPLICATE_STEP_ID.data, DUPLICATE_STEP_ID.render_expectations
        )
