"""Tier 2 presentation contract tests for MessageFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import MessageEvent
from dovo.cli.ui.formatters.events.message import MessageFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

STYLED_MESSAGE = FormatterCase(
    data=MessageEvent(message="Operation finished", style="bold"),
    view=MessageEvent(message="Operation finished", style="bold"),
    render_expectations=["Operation finished"],
)

PLAIN_MESSAGE = FormatterCase(
    data=MessageEvent(message="Running task 'build'...", style=None),
    view=MessageEvent(message="Running task 'build'...", style=None),
    render_expectations=["Running task 'build'..."],
)

MESSAGE_CASES = [
    pytest.param(STYLED_MESSAGE, id="styled_message"),
    pytest.param(PLAIN_MESSAGE, id="plain_message"),
]

MESSAGE_PAYLOAD_CASES = [
    pytest.param(
        STYLED_MESSAGE,
        {
            "message": "Operation finished",
            "style": "bold",
        },
        id="styled_message",
    ),
    pytest.param(
        PLAIN_MESSAGE,
        {
            "message": "Running task 'build'...",
            "style": None,
        },
        id="plain_message",
    ),
]


class MessageFormatterTests:
    """Tier 2 presentation contract tests for MessageFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), MESSAGE_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[MessageEvent, MessageEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(MessageFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", MESSAGE_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[MessageEvent, MessageEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(MessageFormatter, case.data, case.render_expectations)
