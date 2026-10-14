"""Tests for the Claude Code direct-mutation agent adapter registration."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.tool_policy import ToolPolicy
from dovo.core.agents import AgentResponseStatus
from dovo.core.agents.claude import (
    ClaudeAgentAdapter,
    default_claude_run,
    resolve_claude_api_key,
)
from dovo.core.agents.cli_mutation import CliMutationOutcome, CliMutationRunRequest
from dovo.core.agents.registry import PROVIDERS
from tests.harness import AgentRequestBuilder, FakeAgentRunner


class ClaudeDescriptorTests:
    def test_descriptor_matches_pre_determined_data(self) -> None:
        """[tier-1/unit] PROVIDERS['claude']: token 'claude', credential_envs ('ANTHROPIC_API_KEY',), requires_model False, supports_tool_policy True, supports_os_sandbox False, binary 'claude', progress_mapper None, control_envs equal to the eight FR-1 names in order, build is ClaudeAgentAdapter."""
        spec = PROVIDERS["claude"]

        assert (spec.token, spec.credential_envs, spec.requires_model) == ("claude", ("ANTHROPIC_API_KEY",), False)
        assert (spec.supports_tool_policy, spec.supports_os_sandbox) == (True, False)
        assert (spec.binary, spec.progress_mapper, spec.build) == ("claude", None, ClaudeAgentAdapter)
        assert spec.control_envs == (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_MODEL",
            "CLAUDE_CONFIG_DIR",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        )


class ClaudeDefaultRunTests:
    def test_default_run_returns_not_available_error_outcome(self, tmp_path: Path) -> None:
        """[tier-1/unit] default_claude_run: any CliMutationRunRequest returns CliMutationOutcome(status='error', error_detail='Claude Code invocation is not available') with result_text None and denials []."""
        outcome = default_claude_run(
            CliMutationRunRequest(
                worktree_path=tmp_path, prompt="hi", timeout_seconds=3, tools=ToolPolicy(allow_all=True)
            )
        )

        assert outcome == CliMutationOutcome(status="error", error_detail="Claude Code invocation is not available")

    def test_invoke_with_key_returns_provider_error_and_starts_no_process(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] ClaudeAgentAdapter.invoke: ANTHROPIC_API_KEY set returns PROVIDER_ERROR whose errors contain 'Claude Code invocation is not available', and a stream_isolated_process spy is never called."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert any("Claude Code invocation is not available" in error for error in response.errors)
        assert runner.calls == []


class ClaudeCredentialTests:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("  sk-test \n", "sk-test", id="stripped"),
            pytest.param("", None, id="empty"),
            pytest.param("   ", None, id="blank"),
            pytest.param(None, None, id="unset"),
        ],
    )
    def test_resolve_claude_api_key_returns_stripped_value_or_none(
        self, value: str | None, expected: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] resolve_claude_api_key: ANTHROPIC_API_KEY '  sk-test \\n' returns 'sk-test'; empty, whitespace, or unset returns None."""
        if value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", value)

        assert resolve_claude_api_key() == expected

    def test_resolve_claude_api_key_observes_changes_between_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] resolve_claude_api_key: unset returns None, then 'k' set returns 'k', in one process with no module reload."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        before = resolve_claude_api_key()

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        assert (before, resolve_claude_api_key()) == (None, "k")
