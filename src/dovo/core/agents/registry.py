"""Registry of agent provider descriptors.

A token in ``PROVIDERS`` is what the factory can build and what ``validate_config_result`` accepts as ``agent.provider``.
"""

from __future__ import annotations

from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.copilot import COPILOT_TOKEN_ENVS, CopilotAgentAdapter

COPILOT_PROVIDER_SPEC = ProviderSpec(
    token="copilot",
    credential_envs=COPILOT_TOKEN_ENVS,
    requires_model=False,
    supports_tool_policy=False,
    supports_os_sandbox=False,
    build=CopilotAgentAdapter,
    binary="gh",
)

PROVIDERS: dict[str, ProviderSpec] = {spec.token: spec for spec in (COPILOT_PROVIDER_SPEC,)}


def unsupported_provider_message(token: str) -> str:
    """Return the AGENT_PROVIDER_UNSUPPORTED diagnostic naming token and the sorted registered tokens."""
    return (
        f"Unsupported agent provider '{token}' (AGENT_PROVIDER_UNSUPPORTED). Supported: {', '.join(sorted(PROVIDERS))}."
    )
