"""Factory for selecting an agent adapter by provider name."""

from __future__ import annotations

from worktree.core.agents.base import BaseAgentProvider
from worktree.core.agents.registry import PROVIDERS, unsupported_provider_message


def get_agent_adapter(provider: str) -> BaseAgentProvider:
    """Return a new adapter for ``provider``.

    Raises:
        ValueError: When ``provider`` is not registered (``AGENT_PROVIDER_UNSUPPORTED``).
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(unsupported_provider_message(provider))
    return spec.build()
