"""Tier 2 presentation contract tests for SandboxDiffFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.sandbox.sandbox_diff import SandboxDiffFormatter
from dovo.core.sandbox.models import (
    SandboxDiffResult,
    SandboxDiffStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

OK_WITH_DIFF = FormatterCase(
    data=SandboxDiffResult(
        status=SandboxDiffStatus.OK,
        sandbox_id="sbx_diff",
        diff_text="+new_line",
    ),
    view=SandboxDiffResult(
        status=SandboxDiffStatus.OK,
        sandbox_id="sbx_diff",
        diff_text="+new_line",
    ),
    render_expectations=["+new_line"],
)

EMPTY_DIFF = FormatterCase(
    data=SandboxDiffResult(
        status=SandboxDiffStatus.EMPTY_DIFF,
        sandbox_id="sbx_empty",
    ),
    view=SandboxDiffResult(
        status=SandboxDiffStatus.EMPTY_DIFF,
        sandbox_id="sbx_empty",
    ),
    render_expectations=["sbx_empty"],
)

NOT_FOUND = FormatterCase(
    data=SandboxDiffResult(
        status=SandboxDiffStatus.NOT_FOUND,
        sandbox_id="sbx_missing",
        errors=["Sandbox 'sbx_missing' not found."],
        fixes=["Run `dovo sandbox list` to see known sandboxes"],
    ),
    view=SandboxDiffResult(
        status=SandboxDiffStatus.NOT_FOUND,
        sandbox_id="sbx_missing",
        errors=["Sandbox 'sbx_missing' not found."],
        fixes=["Run `dovo sandbox list` to see known sandboxes"],
    ),
    render_expectations=[
        "sbx_missing",
        "Sandbox 'sbx_missing' not found.",
        "Run `dovo sandbox list` to see known sandboxes",
    ],
)

SANDBOX_DIFF_CASES = [
    pytest.param(OK_WITH_DIFF, id="ok_with_diff"),
    pytest.param(EMPTY_DIFF, id="empty_diff"),
    pytest.param(NOT_FOUND, id="not_found"),
]

SANDBOX_DIFF_PAYLOAD_CASES = [
    pytest.param(
        OK_WITH_DIFF,
        {
            "status": "ok",
            "sandbox_id": "sbx_diff",
            "diff_text": "+new_line",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="ok_with_diff",
    ),
    pytest.param(
        EMPTY_DIFF,
        {
            "status": "empty_diff",
            "sandbox_id": "sbx_empty",
            "diff_text": "",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="empty_diff",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "sandbox_id": "sbx_missing",
            "diff_text": "",
            "stat_text": "",
            "files_changed": [],
            "warnings": [],
            "errors": ["Sandbox 'sbx_missing' not found."],
            "error_code": None,
            "fixes": ["Run `dovo sandbox list` to see known sandboxes"],
        },
        id="not_found",
    ),
]


class SandboxDiffFormatterTests:
    """Tier 2 presentation contract tests for SandboxDiffFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), SANDBOX_DIFF_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[SandboxDiffResult, SandboxDiffResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(SandboxDiffFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SANDBOX_DIFF_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[SandboxDiffResult, SandboxDiffResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(SandboxDiffFormatter, case.data, case.render_expectations)
