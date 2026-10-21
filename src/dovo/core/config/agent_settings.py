"""Resolve the settings block of one agent provider."""

from __future__ import annotations

from dovo.core.config.models import AgentSettings, DovoConfig


def agent_settings(config: DovoConfig, provider: str | None = None) -> AgentSettings:
    """Return the agents block of the selected provider.

    A None provider selects config.default_agent. A provider with no block, including a token outside the registry,
    yields the field defaults.
    """
    token = provider or config.default_agent
    return next((settings for name, settings in config.agents.items() if name == token), AgentSettings())
