"""Tier 2 presentation contract tests for SandboxApplyFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.sandbox.sandbox_apply import SandboxApplyFormatter
from dovo.core.sandbox.models import (
    SandboxApplyResult,
    SandboxApplyStatus,
    SandboxApplyStrategy,
)
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

APPLIED_SQUASH = FormatterCase(
    data=SandboxApplyResult(
        status=SandboxApplyStatus.OK,
        sandbox_id="sbx_1",
        strategy=SandboxApplyStrategy.SQUASH,
        commit_sha="abc1234",
        cleaned_up=True,
    ),
    view=SandboxApplyResult(
        status=SandboxApplyStatus.OK,
        sandbox_id="sbx_1",
        strategy=SandboxApplyStrategy.SQUASH,
        commit_sha="abc1234",
        cleaned_up=True,
    ),
    render_expectations=["sbx_1", "squash", "abc1234"],
)

APPLIED_PATCH = FormatterCase(
    data=SandboxApplyResult(
        status=SandboxApplyStatus.OK,
        sandbox_id="sbx_2",
        strategy=SandboxApplyStrategy.PATCH,
        touched_files=["src/main.py"],
        cleaned_up=False,
    ),
    view=SandboxApplyResult(
        status=SandboxApplyStatus.OK,
        sandbox_id="sbx_2",
        strategy=SandboxApplyStrategy.PATCH,
        touched_files=["src/main.py"],
        cleaned_up=False,
    ),
    render_expectations=["sbx_2", "patch"],
)

FAILED_CONFLICT = FormatterCase(
    data=SandboxApplyResult(
        status=SandboxApplyStatus.GIT_FAILED,
        sandbox_id="sbx_3",
        strategy=SandboxApplyStrategy.SQUASH,
        conflicting_files=["src/conflict.py"],
        errors=["Merge conflict in src/conflict.py"],
        fixes=["Resolve manually"],
    ),
    view=SandboxApplyResult(
        status=SandboxApplyStatus.GIT_FAILED,
        sandbox_id="sbx_3",
        strategy=SandboxApplyStrategy.SQUASH,
        conflicting_files=["src/conflict.py"],
        errors=["Merge conflict in src/conflict.py"],
        fixes=["Resolve manually"],
    ),
    render_expectations=["Merge conflict in src/conflict.py", "Resolve manually"],
)

SANDBOX_APPLY_CASES = [
    pytest.param(APPLIED_SQUASH, id="applied_squash"),
    pytest.param(APPLIED_PATCH, id="applied_patch"),
    pytest.param(FAILED_CONFLICT, id="failed_conflict"),
]

SANDBOX_APPLY_PAYLOAD_CASES = [
    pytest.param(
        APPLIED_SQUASH,
        {
            "status": "ok",
            "sandbox_id": "sbx_1",
            "strategy": "squash",
            "touched_files": [],
            "conflicting_files": [],
            "cleaned_up": True,
            "commit_sha": "abc1234",
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="applied_squash",
    ),
    pytest.param(
        APPLIED_PATCH,
        {
            "status": "ok",
            "sandbox_id": "sbx_2",
            "strategy": "patch",
            "touched_files": ["src/main.py"],
            "conflicting_files": [],
            "cleaned_up": False,
            "commit_sha": None,
            "warnings": [],
            "errors": [],
            "error_code": None,
            "fixes": [],
        },
        id="applied_patch",
    ),
    pytest.param(
        FAILED_CONFLICT,
        {
            "status": "git_failed",
            "sandbox_id": "sbx_3",
            "strategy": "squash",
            "touched_files": [],
            "conflicting_files": ["src/conflict.py"],
            "cleaned_up": False,
            "commit_sha": None,
            "warnings": [],
            "errors": ["Merge conflict in src/conflict.py"],
            "error_code": None,
            "fixes": ["Resolve manually"],
        },
        id="failed_conflict",
    ),
]


class SandboxApplyFormatterTests:
    """Tier 2 presentation contract tests for SandboxApplyFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), SANDBOX_APPLY_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[SandboxApplyResult, SandboxApplyResult],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(SandboxApplyFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", SANDBOX_APPLY_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[SandboxApplyResult, SandboxApplyResult]
    ) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(SandboxApplyFormatter, case.data, case.render_expectations)
