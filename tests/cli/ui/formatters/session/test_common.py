"""Contract tests for the shared session formatter helpers."""

from __future__ import annotations

import pytest

from dovo.cli.ui.formatters.session.common import format_step_duration


class FormatStepDurationTests:
    """Contract tests for format_step_duration."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            pytest.param(33.5, "33.5s", id="seconds"),
            pytest.param(0.1, "0.1s", id="lower-boundary"),
            pytest.param(0.04, "<0.1s", id="under-boundary"),
            pytest.param(0.0, "<0.1s", id="zero"),
            pytest.param(-1.0, "-", id="negative"),
            pytest.param(None, "-", id="missing"),
        ],
    )
    def test_format_step_duration(self, seconds: float | None, expected: str) -> None:
        assert format_step_duration(seconds) == expected
