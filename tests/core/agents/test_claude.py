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
    AgentResponse,
    AgentResponseStatus,
    default_tool_policy,
    tool_policy_unsupported_message,
)
from dovo.core.agents.claude import (
    ClaudeAgentAdapter,
    claude_auth_label,
    claude_cli_logged_in,
    default_claude_run,
    resolve_claude_auth_mode,
    resolve_claude_credential,
)
from dovo.core.agents.claude_progress import map_claude_line
from dovo.core.agents.cli_mutation import CliMutationOutcome, CliMutationRunRequest
from dovo.core.agents.registry import PROVIDERS
from tests.harness import (
    AgentRequestBuilder,
    EditingAgentRunner,
    FakeAgentRunner,
    FakeAgentRunnerCall,
    patch_claude_probe,
    real_invocation,
)


class ClaudeDescriptorTests:
    def test_descriptor_declares_the_expected_provider_fields(self) -> None:
        spec = PROVIDERS["claude"]

        assert (spec.token, spec.credential_envs, spec.requires_model) == (
            "claude",
            ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
            False,
        )
        assert (spec.supports_tool_policy, spec.supports_os_sandbox) == (True, False)
        assert (spec.binary, spec.progress_mapper, spec.build) == ("claude", map_claude_line, ClaudeAgentAdapter)
        assert spec.auth_label == claude_auth_label
        assert spec.control_envs == (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_MODEL",
            "CLAUDE_CONFIG_DIR",
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


_DENIED_STDOUT = (
    b'{"type":"result","subtype":"success","is_error":false,"result":"blocked",'
    b'"permission_denials":[{"tool_name":"Bash","tool_use_id":"toolu_1"}]}\n'
)


def _porcelain(repo: Path) -> str:
    """Return `git status --porcelain` output for repo."""
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


def _install_fake_claude_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeAgentRunner) -> None:
    """Set ANTHROPIC_API_KEY, unset CLAUDE_CODE_OAUTH_TOKEN, and install runner at the Claude process boundary."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("dovo.core.agents.cli_mutation.stream_isolated_process", runner)


def _fake_claude(monkeypatch: pytest.MonkeyPatch) -> FakeAgentRunner:
    """Install a FakeAgentRunner at the Claude process boundary with a usable ANTHROPIC_API_KEY and no OAuth token."""
    runner = FakeAgentRunner().returning(stdout=_SUCCESS_STDOUT)
    _install_fake_claude_runner(monkeypatch, runner)

    return runner


def _stub_login_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes = b'{"loggedIn": true}',
    stderr: bytes = b"",
    returncode: int = 0,
    error: Exception | None = None,
) -> list[tuple[list[str], dict[str, object]]]:
    """Replace the probe subprocess with a canned outcome and return its recorded (argv, kwargs) calls."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(argv), kwargs))
        if error is not None:
            raise error

        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    patch_claude_probe(monkeypatch, _run)

    return calls


def _configure_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Set the host environment and login probe so resolve_claude_auth_mode returns the given mode."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if mode == "oauth":
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    elif mode == "key":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    _stub_login_probe(monkeypatch, returncode=0 if mode == "cli" else 1)


def _spawn_in_mode(mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeAgentRunnerCall:
    """Run default_claude_run in the given mode with both credentials in the request env and return the spawn call."""
    runner = _fake_claude(monkeypatch)
    _configure_mode(monkeypatch, mode)
    env = {"ANTHROPIC_API_KEY": "sk-test", "CLAUDE_CODE_OAUTH_TOKEN": "oauth", "PATH": "/bin"}

    default_claude_run(_run_request(tmp_path, env=env))

    return runner.last_call


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

    def test_prompt_goes_to_stdin_as_utf8(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record input_data == 'héllo'.encode('utf-8')."""
        runner = _fake_claude(monkeypatch)

        default_claude_run(_run_request(tmp_path, prompt="héllo"))

        assert runner.last_call.input_data == "héllo".encode()

    def test_process_runs_in_the_worktree_with_the_request_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record cwd == worktree_path and timeout_seconds == 3."""
        runner = _fake_claude(monkeypatch)

        default_claude_run(_run_request(tmp_path))

        assert (runner.last_call.cwd, runner.last_call.timeout_seconds) == (tmp_path, 3)

    def test_prompt_never_appears_on_argv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = _fake_claude(monkeypatch)

        default_claude_run(_run_request(tmp_path, prompt="héllo"))

        assert not any("héllo" in part for part in runner.last_call.cmd)

    def test_unsupported_policy_returns_policy_message_and_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return error with tool_policy_unsupported_message('claude') and zero runner calls for a scratch rule without an invocation."""
        runner = _fake_claude(monkeypatch)
        policy = ToolPolicy(allow=[ToolRule(capability=ToolCapability.READ, pattern="notes/**", root="scratch")])

        outcome = default_claude_run(_run_request(tmp_path, tools=policy))

        assert outcome == CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("claude"))
        assert runner.calls == []

    def test_no_authentication_returns_the_not_authenticated_error_and_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the 'Claude Code is not authenticated' error and zero runner calls with no token, login or key."""
        runner = _fake_claude(monkeypatch)
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        monkeypatch.setattr("dovo.core.agents.claude.shutil.which", lambda _name: "/usr/bin/claude")

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail=(
                "Claude Code is not authenticated. Fix: run `claude auth login`, "
                "export CLAUDE_CODE_OAUTH_TOKEN=... (from `claude setup-token`) or export ANTHROPIC_API_KEY=..."
            ),
        )
        assert runner.calls == []

    def test_missing_binary_with_no_authentication_returns_install_diagnostic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the install diagnostic and zero runner calls when claude is not on PATH and no mode resolves."""
        runner = _fake_claude(monkeypatch)
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        monkeypatch.setattr("dovo.core.agents.claude.shutil.which", lambda _name: None)

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail="claude is not installed or not on PATH. Fix: install Claude Code (https://code.claude.com/docs)",
        )
        assert runner.calls == []

    def test_missing_binary_returns_install_hint_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return the install-hint error when the spawn raises FileNotFoundError('claude')."""
        _fake_claude(monkeypatch).raising(FileNotFoundError("claude"))

        outcome = default_claude_run(_run_request(tmp_path))

        assert outcome == CliMutationOutcome(
            status="error",
            error_detail=(
                "claude is not installed or not on PATH: claude. "
                "Fix: install Claude Code (https://code.claude.com/docs)"
            ),
        )

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

    @pytest.mark.parametrize(
        ("mode", "expects_bare", "expected_env"),
        [
            pytest.param("oauth", False, {"CLAUDE_CODE_OAUTH_TOKEN": "oauth", "PATH": "/bin"}, id="oauth"),
            pytest.param("cli", False, {"PATH": "/bin"}, id="cli"),
            pytest.param("key", True, {"ANTHROPIC_API_KEY": "sk-test", "PATH": "/bin"}, id="key"),
        ],
    )
    def test_auth_mode_decides_the_bare_flag_and_which_credential_is_passed(
        self,
        mode: str,
        expects_bare: bool,
        expected_env: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spawn with '--bare' only for key, passing only the mode's credential from a request env holding both."""
        runner = _fake_claude(monkeypatch)
        _configure_mode(monkeypatch, mode)
        env = {"ANTHROPIC_API_KEY": "sk-test", "CLAUDE_CODE_OAUTH_TOKEN": "oauth", "PATH": "/bin"}

        default_claude_run(_run_request(tmp_path, env=env))

        assert ("--bare" in runner.last_call.cmd, runner.last_call.env) == (expects_bare, expected_env)

    def test_modes_differ_only_in_bare_and_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record identical stdin, cwd, timeout and policy flags across the three modes, with argv differing only by '--bare'."""
        calls = {mode: _spawn_in_mode(mode, tmp_path, monkeypatch) for mode in ("cli", "oauth", "key")}

        without_bare = {mode: [part for part in call.cmd if part != "--bare"] for mode, call in calls.items()}
        shared = {mode: (call.input_data, call.cwd, call.timeout_seconds) for mode, call in calls.items()}
        assert without_bare["cli"] == without_bare["oauth"] == without_bare["key"]
        assert shared["cli"] == shared["oauth"] == shared["key"]
        assert [mode for mode, call in calls.items() if "--bare" in call.cmd] == ["key"]

    @pytest.mark.parametrize(
        "mode", [pytest.param("cli", id="cli"), pytest.param("oauth", id="oauth"), pytest.param("key", id="key")]
    )
    def test_no_credential_value_appears_on_argv(
        self, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn with neither 'oauth' nor 'sk-test' in any argv element."""
        call = _spawn_in_mode(mode, tmp_path, monkeypatch)

        assert not any("oauth" in part or "sk-test" in part for part in call.cmd)

    def test_unwritable_mcp_config_returns_error_and_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return status 'error' with a detail naming the config path and zero runner calls when the write raises OSError."""
        runner = _fake_claude(monkeypatch)
        invocation = real_invocation(tmp_path / "invocation")
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


def _invoke_inherit_with_claude_names(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], AgentResponse]:
    """Invoke in inherit mode with CLAUDE_FOO and CLAUDE_BAR set and only CLAUDE_BAR passed through."""
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

    return runner.last_call.env, response


class ClaudeInvokeTests:
    def test_text_only_success_returns_no_op(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return NO_OP with no diff and raw_text 'done' when the stream succeeds without edits."""
        _fake_claude(monkeypatch)
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert (response.status, response.raw_text, response.unified_diff) == (AgentResponseStatus.NO_OP, "done", None)

    def test_invoke_without_authentication_returns_provider_error_with_the_not_authenticated_detail(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return PROVIDER_ERROR with the wrapped 'Claude Code is not authenticated' detail and zero runner calls."""
        runner = _fake_claude(monkeypatch)
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        monkeypatch.setattr("dovo.core.agents.claude.shutil.which", lambda _name: "/usr/bin/claude")
        request = AgentRequestBuilder().with_worktree_path(git_repo).build()

        response = ClaudeAgentAdapter().invoke(request)

        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.errors == [
            "Agent provider error (AGENT_PROVIDER_ERROR): Claude Code is not authenticated. "
            "Fix: run `claude auth login`, export CLAUDE_CODE_OAUTH_TOKEN=... (from `claude setup-token`) "
            "or export ANTHROPIC_API_KEY=..."
        ]
        assert runner.calls == []

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

        env = runner.last_call.env
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert not set(host) & set(env)

    def test_inherit_mode_drops_unrequested_claude_names_from_the_env(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep CLAUDE_BAR (listed in env_passthrough) and drop CLAUDE_FOO."""
        env, _ = _invoke_inherit_with_claude_names(git_repo, monkeypatch)

        assert (env["CLAUDE_BAR"], "CLAUDE_FOO" in env) == ("bar", False)

    def test_inherit_mode_reports_dropped_claude_names_as_withheld(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Report CLAUDE_FOO, and not CLAUDE_BAR, as withheld in the response and in DOVO_ENV_WITHHELD."""
        env, response = _invoke_inherit_with_claude_names(git_repo, monkeypatch)

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
        invocation = real_invocation(tmp_path / "invocation")
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


def _invoke_rejected_edit(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AgentResponse, Path]:
    """Invoke with two files edited under max_files=1 and return the response and the rejected-patch destination."""
    _install_fake_claude_runner(
        monkeypatch, EditingAgentRunner({"a.txt": "a\n", "b.txt": "b\n"}).returning(stdout=_SUCCESS_STDOUT)
    )
    destination = tmp_path / "rejected" / "code_attempt_1.patch"
    request = (
        AgentRequestBuilder()
        .with_worktree_path(git_repo)
        .with_max_files(1)
        .with_rejected_patch_destination(destination)
        .build()
    )

    return ClaudeAgentAdapter().invoke(request), destination


class ClaudeGitIntegrationTests:
    def test_validated_edit_returns_proposed_patch_and_leaves_it_staged(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return PROPOSED_PATCH with the edited.txt diff, porcelain 'A  edited.txt\\n', and no rejected patch path."""
        _install_fake_claude_runner(
            monkeypatch, EditingAgentRunner({"edited.txt": "edited\n"}).returning(stdout=_SUCCESS_STDOUT)
        )

        response = ClaudeAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert response.status == AgentResponseStatus.PROPOSED_PATCH
        assert response.unified_diff is not None
        assert "diff --git a/edited.txt b/edited.txt" in response.unified_diff
        assert "+edited" in response.unified_diff
        assert _porcelain(git_repo) == "A  edited.txt\n"
        assert response.rejected_patch_path is None

    def test_rejected_diff_is_saved_to_the_destination(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return PROVIDER_ERROR with the saved patch naming a.txt and b.txt and the saved-path error last."""
        response, destination = _invoke_rejected_edit(git_repo, tmp_path, monkeypatch)

        patch_text = destination.read_text(encoding="utf-8")
        assert response.status == AgentResponseStatus.PROVIDER_ERROR
        assert response.rejected_patch_path == str(destination)
        assert "a.txt" in patch_text
        assert "b.txt" in patch_text
        assert response.errors[-1] == f"Discarded edit saved to {destination}."

    def test_rejected_diff_is_rolled_back_from_the_worktree(
        self, git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _invoke_rejected_edit(git_repo, tmp_path, monkeypatch)

        assert _porcelain(git_repo) == ""

    def test_text_only_run_leaves_the_worktree_clean(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_claude(monkeypatch)

        ClaudeAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert _porcelain(git_repo) == ""

    def test_denied_run_with_empty_diff_is_blocked_and_leaves_worktree_clean(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return BLOCKED with no diff, a clean worktree, and the Bash permission-denied error first."""
        _install_fake_claude_runner(monkeypatch, FakeAgentRunner().returning(stdout=_DENIED_STDOUT))

        response = ClaudeAgentAdapter().invoke(AgentRequestBuilder().with_worktree_path(git_repo).build())

        assert response.status == AgentResponseStatus.BLOCKED
        assert response.unified_diff is None
        assert _porcelain(git_repo) == ""
        assert response.errors[0] == (
            "Agent run was blocked by tool permissions (AGENT_PERMISSION_BLOCKED): "
            "Bash: Permission to use Bash was denied"
        )


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
            .with_invocation(real_invocation(tmp_path / "invocation"))
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
            .with_invocation(real_invocation(tmp_path / "invocation"))
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
            .with_invocation(real_invocation(tmp_path / "invocation"))
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
            .with_invocation(real_invocation(tmp_path / "invocation"))
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
        ("token", "key", "expected"),
        [
            pytest.param(" tok ", "k", ("CLAUDE_CODE_OAUTH_TOKEN", "tok"), id="token-wins-stripped"),
            pytest.param("  ", "k", ("ANTHROPIC_API_KEY", "k"), id="blank-token-falls-to-key"),
            pytest.param(None, "  ", None, id="both-blank"),
            pytest.param(None, None, None, id="both-unset"),
        ],
    )
    def test_resolve_claude_credential_returns_first_non_blank_entry_stripped(
        self,
        token: str | None,
        key: str | None,
        expected: tuple[str, str] | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return the first non-blank credential as (name, stripped value), or None when both are blank or unset."""
        for name, value in (("CLAUDE_CODE_OAUTH_TOKEN", token), ("ANTHROPIC_API_KEY", key)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

        assert resolve_claude_credential() == expected

    def test_token_wins_without_consulting_login_probe_or_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return 'oauth' and record zero probe subprocess calls when the token, a login and a key all exist."""
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        probe_calls = _stub_login_probe(monkeypatch)

        assert (resolve_claude_auth_mode(), probe_calls) == ("oauth", [])

    def test_login_wins_over_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return 'cli' when no token exists, the probe reports loggedIn true and a key exists."""
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        _stub_login_probe(monkeypatch)

        assert resolve_claude_auth_mode() == "cli"

    @pytest.mark.parametrize(
        ("key", "expected"),
        [pytest.param("k", "key", id="key-fallback"), pytest.param(None, None, id="nothing")],
    )
    def test_key_is_last_fallback(self, key: str | None, expected: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return 'key' when the probe reports logged out and only the key exists; return None when nothing exists."""
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        if key is None:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        else:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key)

        assert resolve_claude_auth_mode() == expected

    def test_mode_observes_environment_changes_between_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return None, then 'key' after ANTHROPIC_API_KEY is set, then 'oauth' after the token is set."""
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        before = resolve_claude_auth_mode()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        with_key = resolve_claude_auth_mode()
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")

        assert (before, with_key, resolve_claude_auth_mode()) == (None, "key", "oauth")


class ClaudeAuthLabelTests:
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            pytest.param("oauth", "CLAUDE_CODE_OAUTH_TOKEN", id="oauth"),
            pytest.param("cli", "cli session", id="cli"),
            pytest.param("key", "ANTHROPIC_API_KEY", id="key"),
            pytest.param("none", None, id="none"),
        ],
    )
    def test_label_names_the_mode_in_use(
        self, mode: str, expected: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the label of the resolved mode, or None when no authentication is usable."""
        _configure_mode(monkeypatch, mode)

        assert claude_auth_label() == expected


class ClaudeLoginProbeTests:
    @pytest.mark.parametrize(
        ("stdout", "expected"),
        [
            pytest.param(b'{"loggedIn": true}', True, id="logged-in"),
            pytest.param(b'{"loggedIn": false}', False, id="logged-out"),
            pytest.param(b'{"loggedIn": "true"}', False, id="string-true"),
            pytest.param(b'{"loggedIn": 1}', False, id="integer-one"),
            pytest.param(b"{}", False, id="missing-field"),
            pytest.param(b"[]", False, id="not-an-object"),
            pytest.param(b"not json", False, id="not-json"),
            pytest.param(b"", False, id="empty"),
            pytest.param(b"\xff\xfe", False, id="invalid-utf8"),
        ],
    )
    def test_exit_zero_stdout_decides_login(
        self, stdout: bytes, expected: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return True only for a JSON object whose loggedIn is true, and False for every other stdout."""
        _stub_login_probe(monkeypatch, stdout=stdout)

        assert claude_cli_logged_in() is expected

    def test_non_zero_exit_returns_false_even_with_logged_in_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return False for returncode 1 with stdout b'{"loggedIn": true}'."""
        _stub_login_probe(monkeypatch, returncode=1)

        assert claude_cli_logged_in() is False

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(FileNotFoundError("claude"), id="missing-binary"),
            pytest.param(subprocess.TimeoutExpired("claude", 10), id="timeout"),
        ],
    )
    def test_spawn_failure_or_timeout_returns_false(self, error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return False without raising when the probe raises FileNotFoundError or TimeoutExpired."""
        _stub_login_probe(monkeypatch, error=error)

        assert claude_cli_logged_in() is False

    def test_probe_runs_auth_status_with_a_timeout_and_no_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Call subprocess.run with the status argv, a 10-second timeout and shell False."""
        calls = _stub_login_probe(monkeypatch)

        claude_cli_logged_in()

        ((argv, kwargs),) = calls
        assert (argv, kwargs["timeout"], kwargs["shell"]) == (["claude", "auth", "status", "--json"], 10, False)

    def test_probe_env_keeps_path_and_drops_credentials_and_controls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pass PATH == '/bin' and none of the host's credential or control variables to the probe."""
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"):
            monkeypatch.setenv(name, "secret")
        monkeypatch.setenv("PATH", "/bin")
        calls = _stub_login_probe(monkeypatch)

        claude_cli_logged_in()

        env = calls[0][1]["env"]
        assert isinstance(env, dict)
        assert env["PATH"] == "/bin"
        assert not {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"} & set(
            env
        )

    def test_probe_output_never_reaches_log_or_stdout(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Emit nothing to caplog, stdout or stderr when the probe prints secret-looking stdout and stderr."""
        _stub_login_probe(monkeypatch, stdout=b'{"loggedIn": true, "token": "sk-leak"}', stderr=b"sk-leak")

        with caplog.at_level("DEBUG"):
            claude_cli_logged_in()

        captured = capsys.readouterr()
        assert (caplog.text, captured.out, captured.err) == ("", "", "")
