"""Tier 2 presentation contract tests for LockWaitFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import LockWaitEvent
from dovo.cli.ui.formatters.events.lock_wait import LockWaitFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

LOCK_WAIT_WITH_HOLDER = FormatterCase(
    data=LockWaitEvent(lock_path="/path/to/.dovo/.lock", holder_pid="12345", timeout_seconds=30.0),
    view=LockWaitEvent(lock_path="/path/to/.dovo/.lock", holder_pid="12345", timeout_seconds=30.0),
    render_expectations=[".lock", "30.0s", "12345"],
)

LOCK_WAIT_WITHOUT_HOLDER = FormatterCase(
    data=LockWaitEvent(lock_path="/path/to/.dovo/.lock", holder_pid=None, timeout_seconds=15.0),
    view=LockWaitEvent(lock_path="/path/to/.dovo/.lock", holder_pid=None, timeout_seconds=15.0),
    render_expectations=[".lock", "15.0s"],
)

LOCK_WAIT_CASES = [
    pytest.param(LOCK_WAIT_WITH_HOLDER, id="with_holder_pid"),
    pytest.param(LOCK_WAIT_WITHOUT_HOLDER, id="without_holder_pid"),
]

LOCK_WAIT_PAYLOAD_CASES = [
    pytest.param(
        LOCK_WAIT_WITH_HOLDER,
        {
            "lock_path": "/path/to/.dovo/.lock",
            "holder_pid": "12345",
            "timeout_seconds": 30.0,
        },
        id="with_holder_pid",
    ),
    pytest.param(
        LOCK_WAIT_WITHOUT_HOLDER,
        {
            "lock_path": "/path/to/.dovo/.lock",
            "holder_pid": None,
            "timeout_seconds": 15.0,
        },
        id="without_holder_pid",
    ),
]


class LockWaitFormatterTests:
    """Tier 2 presentation contract tests for LockWaitFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), LOCK_WAIT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[LockWaitEvent, LockWaitEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(LockWaitFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", LOCK_WAIT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[LockWaitEvent, LockWaitEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(LockWaitFormatter, case.data, case.render_expectations)
