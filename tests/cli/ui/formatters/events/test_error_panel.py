"""Tier 2 presentation contract tests for ErrorPanelFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import ErrorPanelEvent
from dovo.cli.ui.formatters.events.error_panel import ErrorPanelFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

ERROR_PANEL_DEFAULT_BORDER = FormatterCase(
    data=ErrorPanelEvent(title="Custom Error", message="Something broke", border_style="red"),
    view=ErrorPanelEvent(title="Custom Error", message="Something broke", border_style="red"),
    render_expectations=["Custom Error", "Something broke"],
)

ERROR_PANEL_CUSTOM_BORDER = FormatterCase(
    data=ErrorPanelEvent(title="Fatal Failure", message="Git executable missing", border_style="bold red"),
    view=ErrorPanelEvent(title="Fatal Failure", message="Git executable missing", border_style="bold red"),
    render_expectations=["Fatal Failure", "Git executable missing"],
)

ERROR_PANEL_CASES = [
    pytest.param(ERROR_PANEL_DEFAULT_BORDER, id="default_border"),
    pytest.param(ERROR_PANEL_CUSTOM_BORDER, id="custom_border"),
]

ERROR_PANEL_PAYLOAD_CASES = [
    pytest.param(
        ERROR_PANEL_DEFAULT_BORDER,
        {
            "title": "Custom Error",
            "message": "Something broke",
            "border_style": "red",
        },
        id="default_border",
    ),
    pytest.param(
        ERROR_PANEL_CUSTOM_BORDER,
        {
            "title": "Fatal Failure",
            "message": "Git executable missing",
            "border_style": "bold red",
        },
        id="custom_border",
    ),
]


class ErrorPanelFormatterTests:
    """Tier 2 presentation contract tests for ErrorPanelFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), ERROR_PANEL_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[ErrorPanelEvent, ErrorPanelEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(ErrorPanelFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", ERROR_PANEL_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[ErrorPanelEvent, ErrorPanelEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(ErrorPanelFormatter, case.data, case.render_expectations)
