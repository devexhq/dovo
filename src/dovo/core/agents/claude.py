"""Claude Code direct-mutation agent adapter and runner."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from dovo.common.filesystem import Filesystem
from dovo.common.mcp_servers import McpServerDefinition
from dovo.common.tool_policy import ToolCapability, ToolPolicy
from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.claude_mcp import load_user_claude_mcp_servers
from dovo.core.agents.claude_policy import ClaudePolicyArgs, McpConfigFile, render_claude_policy
from dovo.core.agents.claude_stream import classify_claude_output
from dovo.core.agents.cli_mutation import (
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
    decode_output,
    run_cli_process,
)
from dovo.core.agents.credentials import missing_credential_error, resolve_credential
from dovo.core.agents.environment import WITHHELD_ENV_NAME, format_withheld_report
from dovo.core.agents.models import AgentInvocationContext, AgentRequest
from dovo.core.agents.tools import resolve_policy_roots, tool_policy_unsupported_message

CLAUDE_API_KEY_ENVS = ("ANTHROPIC_API_KEY",)
CLAUDE_CONTROL_ENVS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
ClaudeAuthMode = Literal["key"]

_INSTALL_HINT = "install Claude Code (https://code.claude.com/docs)"
_BASE_ARGV: Final = ("claude", "-p", "--output-format", "stream-json", "--verbose")
_AUTH_FLAGS: Final[Mapping[ClaudeAuthMode, tuple[str, ...]]] = {"key": ("--bare",)}


def resolve_claude_api_key() -> str | None:
    """Resolve the Anthropic API key from the environment at call time."""
    return resolve_credential(CLAUDE_API_KEY_ENVS)


def resolve_claude_auth_mode() -> ClaudeAuthMode | None:
    """Return "key" when ANTHROPIC_API_KEY holds a non-blank value at call time, else None."""
    return "key" if resolve_claude_api_key() is not None else None


def _env_for_mode(env: Mapping[str, str], mode: ClaudeAuthMode) -> dict[str, str]:
    """Return env reduced to the credential the mode passes: ANTHROPIC_API_KEY for "key", with CLAUDE_CODE_OAUTH_TOKEN removed."""
    reduced = dict(env)
    if mode == "key":
        reduced.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    return reduced


def _build_argv(model: str | None, mode: ClaudeAuthMode, policy_flags: tuple[str, ...]) -> list[str]:
    """Return the Claude Code argv: base flags, the mode's flags, session persistence off, an optional model, then the policy flags."""
    argv = [*_BASE_ARGV, *_AUTH_FLAGS[mode], "--no-session-persistence"]
    if model:
        argv.extend(["--model", model])

    return [*argv, *policy_flags]


def _write_mcp_config(config: McpConfigFile) -> str | None:
    """Write the rendered MCP config to its control-directory path, returning the failure detail or None."""
    try:
        Filesystem.atomic_write_text(config.path, config.content)
    except OSError as error:
        return f"Failed to write MCP config {config.path}: {error}"

    return None


def default_claude_run(request: CliMutationRunRequest) -> CliMutationOutcome:
    """Invoke Claude Code headlessly in bare mode with request.env and map its stream-json output into an outcome."""
    mode = resolve_claude_auth_mode()
    if mode is None:
        return CliMutationOutcome(status="error", error_detail=missing_credential_error(CLAUDE_PROVIDER_SPEC))

    rendered = _render_for_request(request.tools, request.worktree_path, request.invocation, request.mcp_servers)
    if isinstance(rendered, str):
        return CliMutationOutcome(status="error", error_detail=tool_policy_unsupported_message("claude"))

    if rendered.mcp_config is not None:
        write_error = _write_mcp_config(rendered.mcp_config)
        if write_error is not None:
            return CliMutationOutcome(status="error", error_detail=write_error)

    completed = run_cli_process(
        _build_argv(request.model, mode, rendered.flags),
        request,
        env=_env_for_mode(request.env, mode),
        binary="claude",
        install_hint=_INSTALL_HINT,
    )
    if isinstance(completed, CliMutationOutcome):
        return completed

    stdout_text, stderr_text = decode_output(completed)

    return classify_claude_output(int(completed.returncode), stdout_text, stderr_text)


def _render_for_request(
    tools: ToolPolicy,
    worktree_path: Path,
    invocation: AgentInvocationContext | None,
    mcp_servers: Mapping[str, McpServerDefinition],
) -> ClaudePolicyArgs | str:
    """Resolve the policy roots for one request and render the policy, returning the first reason it is unsupported."""
    roots = resolve_policy_roots(tools, worktree_path, invocation)
    if isinstance(roots, str):
        return roots

    names_mcp_server = any(rule.capability == ToolCapability.MCP for rule in tools.allow)
    user_servers = load_user_claude_mcp_servers() if names_mcp_server else {}

    return render_claude_policy(tools, roots, configured=mcp_servers, user=user_servers)


def _explicit_claude_name(name: str, request: AgentRequest) -> bool:
    """Return True when name is a declared credential or the user asked for it through step env or agent.env_passthrough (literal or prefix)."""
    if name in CLAUDE_PROVIDER_SPEC.credential_envs or name in request.env:
        return True

    return any(
        name.startswith(entry.removesuffix("*")) if entry.endswith("*") else name == entry
        for entry in request.env_passthrough
    )


class ClaudeAgentAdapter(CliDirectMutationAdapter):
    """Run Claude Code through the shared direct-mutation base."""

    def _policy_unsupported(self, request: AgentRequest) -> str | None:
        """Return the unsupported message when Claude Code cannot preserve the request's policy."""
        rendered = _render_for_request(request.tools, request.worktree_path, request.invocation, request.mcp_servers)

        return tool_policy_unsupported_message("claude") if isinstance(rendered, str) else None

    def _build_run_env(self, request: AgentRequest) -> tuple[dict[str, str], list[str]]:
        """Return the base env minus inherited CLAUDE_* names the user did not ask for, reporting them as withheld."""
        env, withheld = super()._build_run_env(request)
        stripped = [name for name in env if name.startswith("CLAUDE_") and not _explicit_claude_name(name, request)]
        if not stripped:
            return env, withheld

        for name in stripped:
            del env[name]

        merged = sorted({*withheld, *stripped})
        env[WITHHELD_ENV_NAME] = format_withheld_report(merged)

        return env, merged

    def _provider_spec(self) -> ProviderSpec:
        """Return the Claude descriptor."""
        return CLAUDE_PROVIDER_SPEC

    def _provider_name(self) -> str:
        """Return the provider identifier string."""
        return "claude"

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        """Execute Claude Code against the mutation request."""
        return default_claude_run(request)


CLAUDE_PROVIDER_SPEC = ProviderSpec(
    token="claude",
    credential_envs=CLAUDE_API_KEY_ENVS,
    requires_model=False,
    supports_tool_policy=True,
    supports_os_sandbox=False,
    build=ClaudeAgentAdapter,
    binary="claude",
    control_envs=CLAUDE_CONTROL_ENVS,
)
