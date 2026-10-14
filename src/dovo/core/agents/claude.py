"""Claude Code direct-mutation agent adapter registration."""

from __future__ import annotations

from pathlib import Path

from dovo.common.tool_policy import ToolPolicy
from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.claude_policy import ClaudePolicyArgs, render_claude_policy
from dovo.core.agents.cli_mutation import CliDirectMutationAdapter, CliMutationOutcome, CliMutationRunRequest
from dovo.core.agents.credentials import resolve_credential
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
_NOT_AVAILABLE_DETAIL = "Claude Code invocation is not available"


def resolve_claude_api_key() -> str | None:
    """Resolve the Anthropic API key from the environment at call time."""
    return resolve_credential(CLAUDE_API_KEY_ENVS)


def default_claude_run(request: CliMutationRunRequest) -> CliMutationOutcome:
    """Return the not-available error outcome until the invocation slice replaces this runner."""
    return CliMutationOutcome(status="error", error_detail=_NOT_AVAILABLE_DETAIL)


def _render_for_request(
    policy: ToolPolicy, worktree_path: Path, invocation: AgentInvocationContext | None
) -> ClaudePolicyArgs | str:
    """Resolve the policy roots for one request and render the policy, returning the first reason it is unsupported."""
    roots = resolve_policy_roots(policy, worktree_path, invocation)
    if isinstance(roots, str):
        return roots

    return render_claude_policy(policy, roots)


class ClaudeAgentAdapter(CliDirectMutationAdapter):
    """Run Claude Code through the shared direct-mutation base."""

    def _policy_unsupported(self, request: AgentRequest) -> str | None:
        """Return the unsupported message when Claude Code cannot preserve the request's policy."""
        rendered = _render_for_request(request.tools, request.worktree_path, request.invocation)

        return tool_policy_unsupported_message("claude") if isinstance(rendered, str) else None

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
