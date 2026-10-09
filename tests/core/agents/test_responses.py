"""Tests for the shared timeout, provider-error, blocked, and no-op agent response constructors."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from dovo.common.tool_policy import ToolCapability
from dovo.core.agents.models import AgentDenial, AgentResponse, AgentResponseStatus
from dovo.core.agents.responses import (
    blocked_response,
    diagnostics_with_fixes,
    no_op_response,
    provider_error_response,
    timeout_response,
)


class TimeoutResponseTests:
    def test_timeout_response_builds_canonical_diagnostic_and_preserves_metadata(self) -> None:
        """[tier-1/unit] timeout_response: provider="copilot", timeout_seconds=10, duration_ms=250, raw_text="partial", mutation_baseline_ref="abc" returns AgentResponse(status=TIMEOUT, duration_ms=250, raw_text="partial", mutation_baseline_ref="abc", errors=["Agent timed out after 10s (provider=copilot)."], fixes=["Raise timeout_seconds on the agent step"])."""
        response = timeout_response(
            provider="copilot",
            timeout_seconds=10,
            duration_ms=250,
            raw_text="partial",
            mutation_baseline_ref="abc",
        )

        assert response == AgentResponse(
            status=AgentResponseStatus.TIMEOUT,
            duration_ms=250,
            raw_text="partial",
            mutation_baseline_ref="abc",
            errors=["Agent timed out after 10s (provider=copilot)."],
            fixes=["Raise timeout_seconds on the agent step"],
        )

    def test_timeout_response_without_metadata_leaves_it_none(self) -> None:
        """[tier-1/unit] timeout_response: only provider, timeout_seconds and duration_ms supplied returns raw_text and mutation_baseline_ref both None and exactly one error."""
        response = timeout_response(provider="copilot", timeout_seconds=10, duration_ms=250)

        assert response.raw_text is None
        assert response.mutation_baseline_ref is None
        assert len(response.errors) == 1


class BlockedResponseContractTests:
    def test_blocked_response_carries_denials_then_fix(self) -> None:
        """[tier-1/unit] blocked_response: status BLOCKED, errors lead with the AGENT_PERMISSION_BLOCKED line for the first denial then one '<tool>: <message>' per further denial, fixes hold the shell grant snippet for an ungranted bash denial and the remove-or-narrow text for a by_rule denial, duration_ms, raw_text and mutation_baseline_ref preserved."""
        response = blocked_response(
            duration_ms=7,
            denials=[
                AgentDenial(tool="bash", message="no interactive user response", capability=ToolCapability.SHELL),
                AgentDenial(
                    tool="edit",
                    message="denied due to the following rules",
                    capability=ToolCapability.WRITE,
                    by_rule=True,
                ),
            ],
            raw_text="partial",
            mutation_baseline_ref="abc",
        )

        assert response == AgentResponse(
            status=AgentResponseStatus.BLOCKED,
            duration_ms=7,
            raw_text="partial",
            mutation_baseline_ref="abc",
            errors=[
                "Agent run was blocked by tool permissions (AGENT_PERMISSION_BLOCKED): bash: no interactive user response",
                "edit: denied due to the following rules",
            ],
            fixes=[
                "Grant it in the step's tools policy, e.g. tools: {allow: [{capability: shell}]}, "
                "or set it once under agent.tools in .dovo/config.json",
                "Remove or narrow the deny rule that blocked 'edit': denied due to the following rules",
            ],
        )

    @pytest.mark.parametrize(
        ("denials", "expected_fix_count"),
        [
            pytest.param(
                [AgentDenial(tool="bash", message="m", capability=ToolCapability.SHELL)] * 2,
                1,
                id="same-cause-listed-once",
            ),
            pytest.param([AgentDenial(tool="mystery", message="m")], 0, id="unknown-capability-has-no-fix"),
        ],
    )
    def test_blocked_response_lists_one_fix_per_distinct_known_cause(
        self, denials: list[AgentDenial], expected_fix_count: int
    ) -> None:
        """[tier-1/unit] blocked_response: repeated identical denials yield one fix and a denial with no capability and no rule yields none, while every denial still appears in errors."""
        response = blocked_response(duration_ms=1, denials=denials)

        assert len(response.fixes) == expected_fix_count
        assert len(response.errors) == len(denials)

    def test_blocked_response_requires_at_least_one_denial(self) -> None:
        """[tier-1/unit] blocked_response: an empty denials sequence raises ValueError because the first denial leads the error text."""
        with pytest.raises(ValueError):
            blocked_response(duration_ms=1, denials=[])


class ProviderErrorResponseTests:
    @pytest.mark.parametrize(
        ("detail", "errors", "expected"),
        [
            pytest.param("boom", (), ["Agent provider error (AGENT_PROVIDER_ERROR): boom"], id="detail-only"),
            pytest.param(
                None,
                ["Patch touches 2 files; max_files is 1."],
                ["Patch touches 2 files; max_files is 1."],
                id="errors-only-unprefixed",
            ),
            pytest.param(
                "d",
                ["a", "b"],
                ["Agent provider error (AGENT_PROVIDER_ERROR): d", "a", "b"],
                id="detail-first-then-errors-in-order",
            ),
            pytest.param(None, (), [], id="neither"),
            pytest.param("", (), ["Agent provider error (AGENT_PROVIDER_ERROR): "], id="empty-detail-still-prefixed"),
        ],
    )
    def test_provider_error_response_orders_and_prefixes_diagnostics(
        self, detail: str | None, errors: Sequence[str], expected: list[str]
    ) -> None:
        """[tier-1/unit] provider_error_response: (detail, errors) returns status PROVIDER_ERROR with errors == expected, the prefix present exactly once only on detail."""
        response = provider_error_response(duration_ms=1, detail=detail, errors=errors)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == expected

    def test_provider_error_response_does_not_mutate_input_errors(self) -> None:
        """[tier-1/unit] provider_error_response: errors=["a", "b"] with detail="d" leaves the input list == ["a", "b"]."""
        errors = ["a", "b"]

        provider_error_response(duration_ms=1, detail="d", errors=errors)

        assert errors == ["a", "b"]

    def test_provider_error_response_preserves_metadata(self) -> None:
        """[tier-1/unit] provider_error_response: duration_ms=7, raw_text="r", mutation_baseline_ref="abc" are returned unchanged on the response."""
        response = provider_error_response(duration_ms=7, detail="d", raw_text="r", mutation_baseline_ref="abc")

        assert response.duration_ms == 7
        assert response.raw_text == "r"
        assert response.mutation_baseline_ref == "abc"


class NoOpResponseTests:
    def test_no_op_response_preserves_metadata_and_carries_no_diff_or_errors(self) -> None:
        """[tier-1/unit] no_op_response: duration_ms=9, raw_text="r", mutation_baseline_ref="abc" returns status NO_OP with those values preserved, unified_diff None, and errors == []."""
        response = no_op_response(duration_ms=9, raw_text="r", mutation_baseline_ref="abc")

        assert response.status == AgentResponseStatus.NO_OP
        assert response.unified_diff is None
        assert response.errors == []
        assert response.raw_text == "r"
        assert response.mutation_baseline_ref == "abc"
        assert response.duration_ms == 9


class DiagnosticsWithFixesTests:
    @pytest.mark.parametrize(
        ("errors", "fixes", "expected"),
        [
            pytest.param(["e"], ["f1", "f2"], ["e", "Fix:\n- f1\n- f2"], id="errors-and-fixes"),
            pytest.param(["e1", "e2"], [], ["e1", "e2"], id="no-fixes"),
            pytest.param([], ["f"], ["Fix:\n- f"], id="fixes-only"),
        ],
    )
    def test_fixes_form_one_trailing_block(self, errors: list[str], fixes: list[str], expected: list[str]) -> None:
        """[tier-1/unit] diagnostics_with_fixes: errors followed by one 'Fix:' bullet block, and no block when fixes is empty."""
        assert diagnostics_with_fixes(errors, fixes) == expected
