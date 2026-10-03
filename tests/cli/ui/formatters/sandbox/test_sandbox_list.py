"""Tier 2 presentation contract tests for SandboxListFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.sandbox.sandbox_list import SandboxListFormatter
from dovo.core.db import SandboxRecord, SandboxStatus
from dovo.core.sandbox.models import (
    SandboxListResult,
    SandboxListStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = SandboxRecord(
    id="sbx_list1234",
    name="list-sandbox",
    branch_name="worktree/sandbox-list",
    base_commit="abc1234",
    sandbox_path="/tmp/sbx_list1234",
    status=SandboxStatus.ACTIVE,
    created_at="2026-08-31T20:00:00Z",
    updated_at="2026-08-31T20:00:00Z",
)

WITH_SANDBOXES = FormatterCase(
    data=SandboxListResult(status=SandboxListStatus.OK, sandboxes=[_RECORD]),
    view=SandboxListResult(status=SandboxListStatus.OK, sandboxes=[_RECORD]),
    render_expectations=[_RECORD.id, "list-sandbox", _RECORD.branch_name],
)

EMPTY_SANDBOXES = FormatterCase(
    data=SandboxListResult(status=SandboxListStatus.OK, sandboxes=[]),
    view=SandboxListResult(status=SandboxListStatus.OK, sandboxes=[]),
    render_expectations=[],
)

NOT_INITIALIZED = FormatterCase(
    data=SandboxListResult(
        status=SandboxListStatus.NOT_INITIALIZED,
        sandboxes=[],
        errors=["Dovo workspace is not initialized."],
        fixes=["Run `dovo init` to create `.dovo/config.json`"],
    ),
    view=SandboxListResult(
        status=SandboxListStatus.NOT_INITIALIZED,
        sandboxes=[],
        errors=["Dovo workspace is not initialized."],
        fixes=["Run `dovo init` to create `.dovo/config.json`"],
    ),
    render_expectations=["Dovo workspace is not initialized.", "Run `dovo init` to create `.dovo/config.json`"],
)

SANDBOX_LIST_CASES = [
    pytest.param(WITH_SANDBOXES, id="with_sandboxes"),
    pytest.param(EMPTY_SANDBOXES, id="empty_sandboxes"),
    pytest.param(NOT_INITIALIZED, id="not_initialized"),
]

SANDBOX_LIST_PAYLOAD_CASES = [
    pytest.param(
        WITH_SANDBOXES,
        {
            "status": "ok",
            "sandboxes": [
                {
                    "id": "sbx_list1234",
                    "name": "list-sandbox",
                    "branch_name": "worktree/sandbox-list",
                    "base_commit": "abc1234",
                    "sandbox_path": "/tmp/sbx_list1234",
                    "status": "active",
                    "created_at": "2026-08-31T20:00:00Z",
                    "updated_at": "2026-08-31T20:00:00Z",
                }
            ],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="with_sandboxes",
    ),
    pytest.param(
        EMPTY_SANDBOXES,
        {
            "status": "ok",
            "sandboxes": [],
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="empty_sandboxes",
    ),
    pytest.param(
        NOT_INITIALIZED,
        {
            "status": "not_initialized",
            "sandboxes": [],
            "warnings": [],
            "errors": ["Dovo workspace is not initialized."],
            "error_code": None,
            "fixes": ["Run `dovo init` to create `.dovo/config.json`"],
        },
        id="not_initialized",
    ),
]


class SandboxListFormatterTests:
    """Tier 2 presentation contract tests for SandboxListFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), SANDBOX_LIST_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[SandboxListResult, SandboxListResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(SandboxListFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SANDBOX_LIST_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[SandboxListResult, SandboxListResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(SandboxListFormatter, case.data, case.render_expectations)
