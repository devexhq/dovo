"""Tests for config serialization."""

from __future__ import annotations

from dovo.core.config.generator import build_default_config
from dovo.core.config.models import DovoConfig
from dovo.core.config.serialize import serialize_config


class SerializeConfigTests:
    def test_serialize_config_includes_environment_after_concurrency(self) -> None:
        """[tier-1/unit] serialize_config: DovoConfig with sensitive_variables ["A"] returns keys ending [..., "concurrency", "environment"] and result["environment"] == {"sensitive_variables": ["A"]}."""
        payload = build_default_config("demo")
        payload["environment"] = {"sensitive_variables": ["A"]}

        result = serialize_config(DovoConfig.model_validate(payload))

        assert list(result)[-2:] == ["concurrency", "environment"]
        assert result["environment"] == {"sensitive_variables": ["A"]}

    def test_serialize_config_emits_agent_tools_only_when_set(self) -> None:
        """[tier-1/unit] serialize_config: agent.tools is absent from the payload when unset and a policy object when set."""
        payload = build_default_config("demo")
        unset = serialize_config(DovoConfig.model_validate(payload))
        payload["agent"] = {**payload["agent"], "tools": {"allow_all": True}}
        configured = serialize_config(DovoConfig.model_validate(payload))

        assert "tools" not in unset["agent"]
        assert configured["agent"]["tools"] == {"allow": [], "deny": [], "allow_all": True}
