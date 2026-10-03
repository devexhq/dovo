"""Tier 2 presentation contract tests for WarningFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import WarningEvent
from dovo.cli.ui.formatters.events.warning import WarningFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

STANDARD_WARNING = FormatterCase(
    data=WarningEvent(message="Low disk space"),
    view=WarningEvent(message="Low disk space"),
    render_expectations=["Low disk space"],
)

STALE_WARNING = FormatterCase(
    data=WarningEvent(message="Stale worktrees detected: 3"),
    view=WarningEvent(message="Stale worktrees detected: 3"),
    render_expectations=["Stale worktrees detected: 3"],
)

WARNING_CASES = [
    pytest.param(STANDARD_WARNING, id="standard_warning"),
    pytest.param(STALE_WARNING, id="stale_warning"),
]

WARNING_PAYLOAD_CASES = [
    pytest.param(STANDARD_WARNING, {"message": "Low disk space"}, id="standard_warning"),
    pytest.param(STALE_WARNING, {"message": "Stale worktrees detected: 3"}, id="stale_warning"),
]


class WarningFormatterTests:
    """Tier 2 presentation contract tests for WarningFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), WARNING_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WarningEvent, WarningEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(WarningFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", WARNING_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[WarningEvent, WarningEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WarningFormatter, case.data, case.render_expectations)
