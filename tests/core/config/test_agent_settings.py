from __future__ import annotations

import pytest

from dovo.core.config.agent_settings import agent_settings
from dovo.core.config.models import AgentSettings, DovoConfig


def _config(agents: dict[str, object]) -> DovoConfig:
    """Build a DovoConfig with default_agent "copilot" and the given agents map."""
    return DovoConfig.model_validate(
        {"version": 1, "project": {"name": "p"}, "default_agent": "copilot", "agents": agents}
    )


class AgentSettingsTests:
    def test_agent_settings_returns_default_agent_block_without_provider(self) -> None:
        config = _config({"copilot": {"model": "copilot-model"}, "claude": {"model": "claude-model"}})

        assert agent_settings(config) == AgentSettings(model="copilot-model")

    def test_agent_settings_returns_requested_provider_block(self) -> None:
        """Return agents["claude"] while default_agent is "copilot", leaving the copilot block unused."""
        config = _config(
            {"copilot": {"model": "copilot-model"}, "claude": {"model": "claude-model", "temperature": 1.0}}
        )

        assert agent_settings(config, "claude") == AgentSettings(model="claude-model", temperature=1.0)

    @pytest.mark.parametrize(
        "provider",
        [
            pytest.param(None, id="default-agent"),
            pytest.param("claude", id="registered"),
            pytest.param("unregistered", id="unregistered"),
        ],
    )
    def test_agent_settings_returns_field_defaults_when_block_absent(self, provider: str | None) -> None:
        """Return AgentSettings() for a provider with no agents entry, including a token outside the registry."""
        config = _config({})

        assert agent_settings(config, provider) == AgentSettings()
