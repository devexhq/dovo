"""Factory for selecting an agent adapter by provider name."""

from __future__ import annotations

from dovo.core.agents.base import BaseAgentProvider
from dovo.core.agents.registry import PROVIDERS


def get_agent_adapter(provider: str) -> BaseAgentProvider | None:
    """Return a new adapter for ``provider``, or None when ``provider`` is not registered."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        return None
    return spec.build()
