"""Tests for the Claude Code direct-mutation agent adapter registration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.agents import (
    AgentInvocationContext,
    AgentResponseStatus,
    default_tool_policy,
    tool_policy_unsupported_message,
)
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
        outcome = default_claude_run(
            CliMutationRunRequest(
                worktree_path=tmp_path, prompt="hi", timeout_seconds=3, tools=ToolPolicy(allow_all=True)
            )
        )

        assert outcome == CliMutationOutcome(status="error", error_detail="Claude Code invocation is not available")

    def test_invoke_with_key_returns_provider_error_and_starts_no_process(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert any("Claude Code invocation is not available" in error for error in response.errors)
        assert runner.calls == []


def _real_invocation(tmp_path: Path) -> AgentInvocationContext:
    """Create real scratch and control directories outside the worktree."""
    root = tmp_path / "invocation"
    scratch, control = root / "scratch", root / "control"
    scratch.mkdir(parents=True)
    control.mkdir()

    return AgentInvocationContext(
        invocation_id="a" * 32, scratch_path=scratch.resolve(), control_path=control.resolve()
    )


class ClaudePolicyPreflightTests:
    def test_mcp_policy_is_rejected_before_spawn(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.MCP, pattern="server/tool")])
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_invocation(_real_invocation(tmp_path))
            .with_tools(policy)
            .build()
        )

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message("claude")]
        assert runner.calls == []

    @pytest.mark.parametrize(
        ("configured", "user_config"),
        [
            pytest.param({"github": McpServerDefinition(command="npx")}, None, id="configured-server"),
            pytest.param({}, {"mcpServers": {"github": {"command": "uvx"}}}, id="user-config-server"),
        ],
    )
    def test_mcp_allow_for_resolvable_server_passes_preflight(
        self,
        git_repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        configured: dict[str, McpServerDefinition],
        user_config: dict[str, object] | None,
    ) -> None:
        claude_home = tmp_path / "claude-home"
        claude_home.mkdir()
        if user_config is not None:
            (claude_home / ".claude.json").write_text(json.dumps(user_config), encoding="utf-8")

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.MCP, pattern="github/*")])
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_invocation(_real_invocation(tmp_path))
            .with_tools(policy)
            .build()
            .model_copy(update={"mcp_servers": configured})
        )

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert any("Claude Code invocation is not available" in error for error in response.errors)

    def test_narrowed_read_with_read_deny_passes_preflight(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        policy = ToolPolicy(
            allow=[ToolRule(capability=ToolCapability.READ, pattern="src/**")],
            deny=[ToolRule(capability=ToolCapability.READ, pattern="secrets/**")],
        )
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_invocation(_real_invocation(tmp_path))
            .with_tools(policy)
            .build()
        )

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert any("Claude Code invocation is not available" in error for error in response.errors)

    def test_default_policy_with_a_real_invocation_passes_preflight(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_invocation(_real_invocation(tmp_path))
            .with_tools(default_tool_policy())
            .build()
        )

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert any("Claude Code invocation is not available" in error for error in response.errors)

    def test_default_policy_without_an_invocation_is_rejected_before_spawn(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        runner = FakeAgentRunner()
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        request = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(default_tool_policy()).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [tool_policy_unsupported_message("claude")]
        assert runner.calls == []

    def test_supported_policy_passes_preflight_and_reaches_not_available_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git *")])
        request = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(policy).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert any("Claude Code invocation is not available" in error for error in response.errors)


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
        if value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", value)

        assert resolve_claude_api_key() == expected

    def test_resolve_claude_api_key_observes_changes_between_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        before = resolve_claude_api_key()

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        assert (before, resolve_claude_api_key()) == (None, "k")
