"""Tier 2 presentation contract tests for SandboxShowFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.sandbox.sandbox_show import SandboxShowFormatter
from dovo.core.db import SandboxRecord, SandboxStatus
from dovo.core.sandbox.models import (
    SandboxShowResult,
    SandboxShowStatus,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_RECORD = SandboxRecord(
    id="sbx_test1234",
    name="test-sandbox",
    branch_name="worktree/sandbox-test",
    base_commit="abc1234",
    sandbox_path="/tmp/sbx_test1234",
    status=SandboxStatus.ACTIVE,
    created_at="2026-08-31T20:00:00Z",
    updated_at="2026-08-31T20:00:00Z",
)

FOUND_ACTIVE = FormatterCase(
    data=SandboxShowResult(
        status=SandboxShowStatus.OK,
        sandbox=_RECORD,
        disk_present=True,
    ),
    view=SandboxShowResult(
        status=SandboxShowStatus.OK,
        sandbox=_RECORD,
        disk_present=True,
    ),
    render_expectations=[_RECORD.id, "test-sandbox", _RECORD.branch_name, _RECORD.base_commit],
)

RECONCILED_MISSING_DISK = FormatterCase(
    data=SandboxShowResult(
        status=SandboxShowStatus.OK,
        sandbox=_RECORD,
        disk_present=False,
        reconciled=True,
    ),
    view=SandboxShowResult(
        status=SandboxShowStatus.OK,
        sandbox=_RECORD,
        disk_present=False,
        reconciled=True,
    ),
    render_expectations=[_RECORD.id, "test-sandbox", _RECORD.branch_name, _RECORD.base_commit],
)

NOT_FOUND = FormatterCase(
    data=SandboxShowResult(
        status=SandboxShowStatus.NOT_FOUND,
        errors=["Sandbox 'sbx_missing' not found."],
        fixes=["Run `dovo sandbox list` to see known sandboxes"],
    ),
    view=SandboxShowResult(
        status=SandboxShowStatus.NOT_FOUND,
        errors=["Sandbox 'sbx_missing' not found."],
        fixes=["Run `dovo sandbox list` to see known sandboxes"],
    ),
    render_expectations=["Sandbox 'sbx_missing' not found.", "Run `dovo sandbox list` to see known sandboxes"],
)

SANDBOX_SHOW_CASES = [
    pytest.param(FOUND_ACTIVE, id="found_active"),
    pytest.param(RECONCILED_MISSING_DISK, id="reconciled_missing_disk"),
    pytest.param(NOT_FOUND, id="not_found"),
]

SANDBOX_SHOW_PAYLOAD_CASES = [
    pytest.param(
        FOUND_ACTIVE,
        {
            "status": "ok",
            "sandbox": {
                "id": "sbx_test1234",
                "name": "test-sandbox",
                "branch_name": "worktree/sandbox-test",
                "base_commit": "abc1234",
                "sandbox_path": "/tmp/sbx_test1234",
                "status": "active",
                "created_at": "2026-08-31T20:00:00Z",
                "updated_at": "2026-08-31T20:00:00Z",
            },
            "disk_present": True,
            "reconciled": False,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="found_active",
    ),
    pytest.param(
        RECONCILED_MISSING_DISK,
        {
            "status": "ok",
            "sandbox": {
                "id": "sbx_test1234",
                "name": "test-sandbox",
                "branch_name": "worktree/sandbox-test",
                "base_commit": "abc1234",
                "sandbox_path": "/tmp/sbx_test1234",
                "status": "active",
                "created_at": "2026-08-31T20:00:00Z",
                "updated_at": "2026-08-31T20:00:00Z",
            },
            "disk_present": False,
            "reconciled": True,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="reconciled_missing_disk",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "sandbox": None,
            "disk_present": False,
            "reconciled": False,
            "warnings": [],
            "errors": ["Sandbox 'sbx_missing' not found."],
            "error_code": None,
            "fixes": ["Run `dovo sandbox list` to see known sandboxes"],
        },
        id="not_found",
    ),
]


class SandboxShowFormatterTests:
    """Tier 2 presentation contract tests for SandboxShowFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), SANDBOX_SHOW_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[SandboxShowResult, SandboxShowResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(SandboxShowFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SANDBOX_SHOW_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[SandboxShowResult, SandboxShowResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(SandboxShowFormatter, case.data, case.render_expectations)
