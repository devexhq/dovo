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

STANDARD_VERSION = FormatterCase(
    data=WelcomeBannerEvent(version="0.1.0"),
    view=WelcomeBannerEvent(version="0.1.0"),
    render_expectations=["v0.1.0"],
)

PRERELEASE_VERSION = FormatterCase(
    data=WelcomeBannerEvent(version="1.2.3-rc.1"),
    view=WelcomeBannerEvent(version="1.2.3-rc.1"),
    render_expectations=["v1.2.3-rc.1"],
)

BANNER_CASES = [
    pytest.param(STANDARD_VERSION, id="standard_version"),
    pytest.param(PRERELEASE_VERSION, id="prerelease_version"),
]

BANNER_PAYLOAD_CASES = [
    pytest.param(STANDARD_VERSION, {"version": "0.1.0"}, id="standard_version"),
    pytest.param(PRERELEASE_VERSION, {"version": "1.2.3-rc.1"}, id="prerelease_version"),
]


class WelcomeBannerFormatterTests:
    """Tier 2 presentation contract tests for WelcomeBannerFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), BANNER_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[WelcomeBannerEvent, WelcomeBannerEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(WelcomeBannerFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", BANNER_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[WelcomeBannerEvent, WelcomeBannerEvent]
    ) -> None:
        """Verify non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(WelcomeBannerFormatter, case.data, case.render_expectations)
