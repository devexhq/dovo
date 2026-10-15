"""Tests for the Claude Code direct-mutation agent adapter and runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem
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
    resolve_claude_auth_mode,
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


_SUCCESS_STDOUT = b'{"type":"result","subtype":"success","is_error":false,"result":"done"}\n'
_ALLOW_ALL_ARGV = [
    "claude",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--bare",
    "--no-session-persistence",
    "--permission-mode",
    "bypassPermissions",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
]


def _fake_claude(monkeypatch: pytest.MonkeyPatch) -> FakeAgentRunner:
    """Install a FakeAgentRunner at the Claude process boundary with a usable ANTHROPIC_API_KEY."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    runner = FakeAgentRunner().returning(stdout=_SUCCESS_STDOUT)
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)

    return runner


def _run_request(
    worktree_path: Path,
    *,
    prompt: str = "hi",
    model: str | None = None,
    tools: ToolPolicy | None = None,
    invocation: AgentInvocationContext | None = None,
    env: dict[str, str] | None = None,
    mcp_servers: dict[str, McpServerDefinition] | None = None,
) -> CliMutationRunRequest:
    """Build a run request with a 3-second timeout and an allow-all policy unless tools is given."""
    return CliMutationRunRequest(
        worktree_path=worktree_path,
        prompt=prompt,
        model=model,
        timeout_seconds=3,
        tools=tools if tools is not None else ToolPolicy(allow_all=True),
        invocation=invocation,
        env=env or {},
        mcp_servers=mcp_servers or {},
    )


class ClaudeRunTests:
    @pytest.mark.parametrize(
        ("model", "expected_argv"),
        [
            pytest.param(None, _ALLOW_ALL_ARGV, id="no-model"),
            pytest.param(
                "sonnet",
                [*_ALLOW_ALL_ARGV[:7], "--model", "sonnet", *_ALLOW_ALL_ARGV[7:]],
                id="model-before-policy-flags",
            ),
        ],
    )
    def test_argv_is_bare_base_flags_then_optional_model_then_policy_flags(
        self, model: str | None, expected_argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn the bare-mode argv for an allow_all policy, with '--model' directly after '--no-session-persistence'."""
        runner = _fake_claude(monkeypatch)

        default_claude_run(_run_request(tmp_path, model=model))

        assert runner.last_call.cmd == expected_argv

    def test_prompt_goes_to_stdin_as_utf8_with_worktree_cwd_and_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record input_data == 'héllo'.encode('utf-8'), cwd == worktree_path, timeout_seconds == 3, and no prompt in argv."""
        runner = _fake_claude(monkeypatch)

        default_claude_run(_run_request(tmp_path, prompt="héllo"))

        call = runner.last_call
        assert call.input_data == "héllo".encode()
        assert (call.cwd, call.timeout_seconds) == (tmp_path, 3)
        assert not any("héllo" in part for part in call.cmd)

    def test_unsupported_policy_returns_policy_message_and_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error with tool_policy_unsupported_message('claude') and zero runner calls for a scratch rule without an invocation."""
        runner = _fake_claude(monkeypatch)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.READ, pattern="notes/**", root="scratch")])

        outcome = default_claude_run(_run_request(tmp_path, tools=policy))

        assert outcome == CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("claude"))
        assert runner.calls == []

    def test_missing_key_returns_registration_diagnostic_and_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error 'missing ANTHROPIC_API_KEY. Fix: export ANTHROPIC_API_KEY=...' with zero runner calls."""
        runner = _fake_claude(monkeypatch)
        monkeypatch.delenv("ANTHROPIC_API_KEY")

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(
            status="error", error_detail="missing ANTHROPIC_API_KEY. Fix: export ANTHROPIC_API_KEY=..."
        )
        assert runner.calls == []

    def test_missing_binary_returns_install_hint_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return the install-hint error when the spawn raises FileNotFoundError('claude')."""
        runner = _fake_claude(monkeypatch).raising(FileNotFoundError("claude"))

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail=(
                "claude is not installed or not on PATH: claude. "
                "Fix: install Claude Code (https://code.claude.com/docs)"
            ),
        )
        assert len(runner.calls) == 1

    def test_unknown_option_stderr_returns_update_required_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the update-required error for exit 1 with 'error: unknown option' on stderr."""
        _fake_claude(monkeypatch).returning(returncode=1, stderr=b"error: unknown option '--bare'")

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="Installed Claude Code lacks a required flag. Fix: run `claude update` or reinstall Claude Code.",
        )

    def test_timeout_returns_timeout_outcome(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return CliMutationOutcome(status='timeout') when the spawn raises subprocess.TimeoutExpired."""
        _fake_claude(monkeypatch).raising(subprocess.TimeoutExpired("claude", 3))

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(status="timeout")

    def test_exit_zero_success_result_returns_finished_with_result_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return status 'finished' with result_text 'done' for exit 0 and a success result event."""
        _fake_claude(monkeypatch)

        outcome = default_claude_run(_run_request(tmp_path))

        assert (outcome.status, outcome.result_text, outcome.error_detail) == ("finished", "done", None)

    def test_oauth_token_in_request_env_never_reaches_the_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn with ANTHROPIC_API_KEY kept and CLAUDE_CODE_OAUTH_TOKEN dropped from the request env."""
        runner = _fake_claude(monkeypatch)
        env = {"ANTHROPIC_API_KEY": "sk-test", "CLAUDE_CODE_OAUTH_TOKEN": "oauth", "PATH": "/bin"}

        default_claude_run(_run_request(tmp_path, env=env))

        assert runner.last_call.env == {"ANTHROPIC_API_KEY": "sk-test", "PATH": "/bin"}

    def test_unwritable_mcp_config_returns_error_and_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return status 'error' with a detail naming the config path and zero runner calls when the write raises OSError."""
        runner = _fake_claude(monkeypatch)
        invocation = _real_invocation(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.setattr(Filesystem, "atomic_write_text", _raise_disk_full)
        request = _run_request(
            worktree,
            tools=ToolPolicy(allow=[ToolRule(capability=ToolCapability.MCP, pattern="github/*")]),
            invocation=invocation,
            mcp_servers={"github": McpServerDefinition(command="npx")},
        )

        outcome = default_claude_run(request)

        assert outcome.status == "error"
        assert str(invocation.control_path / "mcp-config.json") in (outcome.error_detail or "")
        assert runner.calls == []


def _raise_disk_full(path: Path, text: str) -> None:
    raise OSError("disk full")


class ClaudeInvokeTests:
    def test_text_only_success_returns_no_op(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return NO_OP with raw_text 'done' when the stream succeeds and the worktree diff is empty."""
        _fake_claude(monkeypatch)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert (response.status, response.raw_text) == (AgentResponseStatus.NO_OP, "done")

    def test_allowlist_spawn_env_has_only_the_approved_key_and_no_claude_or_control_names(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record an env with ANTHROPIC_API_KEY and none of the host's CLAUDE_*, control, or other credential names."""
        runner = _fake_claude(monkeypatch)
        host = {
            "CLAUDE_CONFIG_DIR": "/home/x/.claude",
            "CLAUDE_FOO": "1",
            "ANTHROPIC_AUTH_TOKEN": "token",
            "ANTHROPIC_BASE_URL": "https://proxy.invalid",
            "GH_TOKEN": "gh-secret",
        }
        for name, value in host.items():
            monkeypatch.setenv(name, value)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()

        ClaudeAgentAdapter().invoke(request)

        call = runner.last_call
        assert call.env["ANTHROPIC_API_KEY"] == "sk-test"
        assert not set(host) & set(call.env)
        assert not any("sk-test" in part for part in call.cmd)

    def test_inherit_mode_strips_unrequested_claude_names_and_reports_them(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep CLAUDE_BAR (listed in env_passthrough), drop CLAUDE_FOO, and report CLAUDE_FOO as withheld."""
        runner = _fake_claude(monkeypatch)
        monkeypatch.setenv("CLAUDE_FOO", "foo")
        monkeypatch.setenv("CLAUDE_BAR", "bar")
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_env_mode("inherit")
            .with_env_passthrough(["CLAUDE_BAR"])
            .build()
        )

        response = ClaudeAgentAdapter().invoke(request)

        env = runner.last_call.env
        assert (env["CLAUDE_BAR"], "CLAUDE_FOO" in env) == ("bar", False)
        assert "CLAUDE_FOO" in response.env_withheld
        assert "CLAUDE_BAR" not in response.env_withheld
        assert "CLAUDE_FOO" in env["DOVO_ENV_WITHHELD"].split(",")

    def test_step_env_claude_name_is_kept_and_not_reported(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record CLAUDE_CODE_MAX_OUTPUT_TOKENS == '8000' in the env and no withheld entry for it in inherit mode."""
        runner = _fake_claude(monkeypatch)
        monkeypatch.setenv("CLAUDE_FOO", "foo")
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_env_mode("inherit")
            .with_env({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"})
            .build()
        )

        response = ClaudeAgentAdapter().invoke(request)

        env = runner.last_call.env
        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
        assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in response.env_withheld
        assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env["DOVO_ENV_WITHHELD"].split(",")

    def test_mcp_allow_writes_rendered_config_before_spawn(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Find <control>/mcp-config.json holding the configured server when the runner is called."""
        invocation = _real_invocation(tmp_path)
        config_path = invocation.control_path / "mcp-config.json"
        runner = _ConfigSnapshotRunner(config_path).returning(stdout=_SUCCESS_STDOUT)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.MCP, pattern="github/*")])
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_invocation(invocation)
            .with_tools(policy)
            .build()
            .model_copy(update={"mcp_servers": {"github": McpServerDefinition(command="npx")}})
        )

        ClaudeAgentAdapter().invoke(request)

        assert runner.snapshot is not None
        assert json.loads(runner.snapshot) == {"mcpServers": {"github": {"command": "npx"}}}


class _ConfigSnapshotRunner(FakeAgentRunner):
    """FakeAgentRunner that reads the MCP config file at the moment the process would start."""

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self._config_path = config_path
        self.snapshot: str | None = None

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input_data: bytes,
        timeout_seconds: float,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if self._config_path.exists():
            self.snapshot = self._config_path.read_text(encoding="utf-8")

        return super().__call__(cmd, cwd=cwd, env=env, input_data=input_data, timeout_seconds=timeout_seconds, **kwargs)


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

        runner = _fake_claude(monkeypatch)
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

        assert response.status == AgentResponseStatus.NO_OP
        assert len(runner.calls) == 1

    def test_narrowed_read_with_read_deny_passes_preflight(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_claude(monkeypatch)
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

        assert response.status == AgentResponseStatus.NO_OP
        assert len(runner.calls) == 1

    def test_default_policy_with_a_real_invocation_passes_preflight(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_claude(monkeypatch)
        request = (
            AgentRequestBuilder()
            .with_worktree_path(git_repo)
            .with_invocation(_real_invocation(tmp_path))
            .with_tools(default_tool_policy())
            .build()
        )

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.NO_OP
        assert len(runner.calls) == 1

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

    def test_supported_policy_passes_preflight_and_reaches_the_runner(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _fake_claude(monkeypatch)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git *")])
        request = AgentRequestBuilder().with_worktree_path(git_repo).with_tools(policy).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.NO_OP
        assert len(runner.calls) == 1


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

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("sk-test", "key", id="key"),
            pytest.param("   ", None, id="blank"),
            pytest.param(None, None, id="unset"),
        ],
    )
    def test_resolve_claude_auth_mode_follows_key_at_call_time(
        self, value: str | None, expected: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return 'key' for a non-blank ANTHROPIC_API_KEY and None for blank or unset."""
        if value is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", value)

        assert resolve_claude_auth_mode() == expected

    def test_resolve_claude_api_key_observes_changes_between_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        before = resolve_claude_api_key()

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        assert (before, resolve_claude_api_key()) == (None, "k")
