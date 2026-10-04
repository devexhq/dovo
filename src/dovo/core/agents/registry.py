"""Registry of agent provider descriptors.

A token in ``PROVIDERS`` is what the factory can build and what ``validate_config_result`` accepts as ``agent.provider``.
"""

from __future__ import annotations

from dovo.core.agents.base import ProviderSpec
from dovo.core.agents.copilot import COPILOT_PROVIDER_SPEC

PROVIDERS: dict[str, ProviderSpec] = {spec.token: spec for spec in (COPILOT_PROVIDER_SPEC,)}


def unsupported_provider_message(token: str) -> str:
    """Return the AGENT_PROVIDER_UNSUPPORTED diagnostic naming token and the sorted registered tokens."""
    return (
        f"Unsupported agent provider '{token}' (AGENT_PROVIDER_UNSUPPORTED). Supported: {', '.join(sorted(PROVIDERS))}."
    )
