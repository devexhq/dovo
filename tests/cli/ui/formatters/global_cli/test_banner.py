"""Tier 2 presentation contract tests for WelcomeBannerFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import WelcomeBannerEvent
from dovo.cli.ui.formatters.global_cli.banner import WelcomeBannerFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

STANDARD_VERSION = FormatterCase.identity(
    data=WelcomeBannerEvent(version="0.1.0"),
    render_expectations=["v0.1.0"],
)

BANNER_CASES = [
    pytest.param(STANDARD_VERSION, id="standard_version"),
]

BANNER_PAYLOAD_CASES = [
    pytest.param(STANDARD_VERSION, {"version": "0.1.0"}, id="standard_version"),
]


class WelcomeBannerFormatterTests:
    """Tier 2 presentation contract tests for WelcomeBannerFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), BANNER_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WelcomeBannerEvent, WelcomeBannerEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(WelcomeBannerFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", BANNER_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WelcomeBannerEvent, WelcomeBannerEvent]
    ) -> None:
        assert_rich_render_shows_every_view_value(WelcomeBannerFormatter, case.data, case.render_expectations)
