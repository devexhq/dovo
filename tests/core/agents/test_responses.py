"""Tests for the shared timeout, provider-error, and no-op agent response constructors."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from dovo.core.agents.models import AgentResponse, AgentResponseStatus
from dovo.core.agents.responses import no_op_response, provider_error_response, timeout_response


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
