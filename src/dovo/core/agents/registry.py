"""Registry of agent provider descriptors.

A token in ``PROVIDERS`` is what the factory can build and what ``validate_config_result`` accepts as ``agent.provider``.
"""

from __future__ import annotations

from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.copilot import COPILOT_TOKEN_ENVS, CopilotAgentAdapter
from dovo.core.agents.cursor import CURSOR_API_KEY_ENV, CursorAgentAdapter
from dovo.core.agents.gemini import GEMINI_API_KEY_ENV, GeminiAgentAdapter
from dovo.core.agents.local import LocalAgentAdapter
from dovo.core.agents.models import ProviderKind
from dovo.core.agents.ollama import OllamaAgentAdapter

LOCAL_PROVIDER_SPEC = ProviderSpec(
    token="local",
    kind=ProviderKind.DIFF_RETURNING,
    credential_envs=(),
    requires_model=False,
    supports_tool_policy=False,
    supports_os_sandbox=False,
    build=LocalAgentAdapter,
)

OLLAMA_PROVIDER_SPEC = ProviderSpec(
    token="ollama",
    kind=ProviderKind.DIFF_RETURNING,
    credential_envs=(),
    requires_model=True,
    supports_tool_policy=False,
    supports_os_sandbox=False,
    build=OllamaAgentAdapter,
)

CURSOR_PROVIDER_SPEC = ProviderSpec(
    token="cursor",
    kind=ProviderKind.DIRECT_MUTATION,
    credential_envs=(CURSOR_API_KEY_ENV,),
    requires_model=True,
    supports_tool_policy=False,
    supports_os_sandbox=False,
    build=CursorAgentAdapter,
)

GEMINI_PROVIDER_SPEC = ProviderSpec(
    token="gemini",
    kind=ProviderKind.DIRECT_MUTATION,
    credential_envs=(GEMINI_API_KEY_ENV,),
    requires_model=False,
    supports_tool_policy=False,
    supports_os_sandbox=False,
    build=GeminiAgentAdapter,
    binary="gemini",
)

COPILOT_PROVIDER_SPEC = ProviderSpec(
    token="copilot",
    kind=ProviderKind.DIRECT_MUTATION,
    credential_envs=COPILOT_TOKEN_ENVS,
    requires_model=False,
    supports_tool_policy=False,
    supports_os_sandbox=False,
    build=CopilotAgentAdapter,
    binary="gh",
)

PROVIDERS: dict[str, ProviderSpec] = {
    spec.token: spec
    for spec in (
        LOCAL_PROVIDER_SPEC,
        OLLAMA_PROVIDER_SPEC,
        CURSOR_PROVIDER_SPEC,
        GEMINI_PROVIDER_SPEC,
        COPILOT_PROVIDER_SPEC,
    )
}


def unsupported_provider_message(token: str) -> str:
    """Return the AGENT_PROVIDER_UNSUPPORTED diagnostic naming token and the sorted registered tokens."""
    return (
        f"Unsupported agent provider '{token}' (AGENT_PROVIDER_UNSUPPORTED). Supported: {', '.join(sorted(PROVIDERS))}."
    )
