"""Tests for the shared timeout, provider-error, blocked, unfixable, and no-op agent response constructors."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from dovo.common.models import Remediation
from dovo.common.tool_policy import ToolCapability
from dovo.core.agents.models import AgentDenial, AgentResponse, AgentResponseStatus
from dovo.core.agents.responses import (
    blocked_response,
    diagnostics_with_remediations,
    no_op_response,
    provider_error_response,
    timeout_response,
    unfixable_response,
)


class TimeoutResponseTests:
    def test_timeout_response_builds_canonical_diagnostic_and_preserves_metadata(self) -> None:
        response = timeout_response(
            provider="copilot",
            timeout_seconds=10,
            duration_seconds=250.0,
            raw_text="partial",
            mutation_baseline_ref="abc",
        )

        assert response == AgentResponse(
            status=AgentResponseStatus.TIMEOUT,
            duration_seconds=250.0,
            raw_text="partial",
            mutation_baseline_ref="abc",
            errors=["Agent timed out after 10s (provider=copilot)."],
            remediations=[Remediation(message="Raise 'timeout_seconds' on the agent step")],
        )

    def test_timeout_response_without_metadata_leaves_it_none(self) -> None:
        response = timeout_response(provider="copilot", timeout_seconds=10, duration_seconds=250.0)

        assert response.raw_text is None
        assert response.mutation_baseline_ref is None
        assert len(response.errors) == 1


class BlockedResponseContractTests:
    def test_blocked_response_carries_denials_then_remediations(self) -> None:
        response = blocked_response(
            provider="claude",
            duration_seconds=7.0,
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
            duration_seconds=7.0,
            raw_text="partial",
            mutation_baseline_ref="abc",
            errors=[
                "Agent run was blocked by tool permissions (AGENT_PERMISSION_BLOCKED): bash: no interactive user response",
                "edit: denied due to the following rules",
            ],
            remediations=[
                Remediation(
                    message=(
                        "Grant it in the step's tools policy, e.g. 'tools: {allow: [{capability: shell}]}', "
                        "or set it once under 'agents.claude.tools' in '.dovo/config.json' "
                        "(see 'docs/guides/agent-providers.md#tool-policy-profiles')"
                    )
                ),
                Remediation(
                    message="Remove or narrow the deny rule that blocked 'edit': denied due to the following rules"
                ),
            ],
        )

    @pytest.mark.parametrize(
        ("denials", "expected_remediation_count"),
        [
            pytest.param(
                [AgentDenial(tool="bash", message="m", capability=ToolCapability.SHELL)] * 2,
                1,
                id="same-cause-listed-once",
            ),
            pytest.param([AgentDenial(tool="mystery", message="m")], 0, id="unknown-capability-has-no-remediation"),
        ],
    )
    def test_blocked_response_lists_one_remediation_per_distinct_known_cause(
        self, denials: list[AgentDenial], expected_remediation_count: int
    ) -> None:
        response = blocked_response(provider="copilot", duration_seconds=1.0, denials=denials)

        assert len(response.remediations) == expected_remediation_count
        assert len(response.errors) == len(denials)

    def test_blocked_response_requires_at_least_one_denial(self) -> None:
        with pytest.raises(ValueError):
            blocked_response(provider="copilot", duration_seconds=1.0, denials=[])


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
        response = provider_error_response(duration_seconds=1.0, detail=detail, errors=errors)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == expected

    def test_provider_error_response_does_not_mutate_input_errors(self) -> None:
        errors = ["a", "b"]

        provider_error_response(duration_seconds=1.0, detail="d", errors=errors)

        assert errors == ["a", "b"]

    def test_provider_error_response_preserves_metadata(self) -> None:
        response = provider_error_response(duration_seconds=7.0, detail="d", raw_text="r", mutation_baseline_ref="abc")

        assert response.duration_seconds == 7.0
        assert response.raw_text == "r"
        assert response.mutation_baseline_ref == "abc"


class NoOpResponseTests:
    def test_no_op_response_preserves_metadata_and_carries_no_diff_or_errors(self) -> None:
        response = no_op_response(duration_seconds=9.0, raw_text="r", mutation_baseline_ref="abc")

        assert response.status == AgentResponseStatus.NO_OP
        assert response.unified_diff is None
        assert response.errors == []
        assert response.raw_text == "r"
        assert response.mutation_baseline_ref == "abc"
        assert response.duration_seconds == 9.0


class UnfixableResponseTests:
    def test_unfixable_response_carries_reason_and_errors(self) -> None:
        response = unfixable_response(
            reason="no SSH key",
            duration_seconds=11.0,
            errors=["Discarded edit saved to /tmp/x.patch."],
            raw_text="r",
            mutation_baseline_ref="abc",
        )

        assert response == AgentResponse(
            status=AgentResponseStatus.UNFIXABLE,
            duration_seconds=11.0,
            unfixable_reason="no SSH key",
            errors=["Discarded edit saved to /tmp/x.patch."],
            raw_text="r",
            mutation_baseline_ref="abc",
        )


class DiagnosticsWithRemediationsTests:
    @pytest.mark.parametrize(
        ("errors", "remediations", "expected"),
        [
            pytest.param(
                ["e"],
                [Remediation(message="Do one"), Remediation(message="Run 'two'", command="two")],
                ["e", "Fix:\n- Do one\n- Run 'two'"],
                id="errors-and-remediations",
            ),
            pytest.param(["e1", "e2"], [], ["e1", "e2"], id="errors-only"),
            pytest.param([], [Remediation(message="Do it")], ["Fix:\n- Do it"], id="remediations-only"),
        ],
    )
    def test_appends_one_fix_block(
        self, errors: list[str], remediations: list[Remediation], expected: list[str]
    ) -> None:
        """Return errors plus 'Fix:\\n- <m1>\\n- <m2>' only when remediations are present."""
        assert diagnostics_with_remediations(errors, remediations) == expected
