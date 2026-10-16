"""Tests for config serialization."""

from __future__ import annotations

from dovo.core.config.generator import build_default_config
from dovo.core.config.models import DovoConfig
from dovo.core.config.serialize import serialize_config


class SerializeConfigTests:
    def test_serialize_config_emits_top_level_keys_in_normative_order(self) -> None:
        payload = build_default_config("demo")
        payload["environment"] = {"sensitive_variables": ["A"]}

        result = serialize_config(DovoConfig.model_validate(payload))

        assert list(result) == [
            "version",
            "project",
            "worktree",
            "default_agent",
            "agents",
            "history",
            "doctor",
            "prune",
            "telemetry",
            "concurrency",
            "environment",
        ]
        assert result["environment"] == {"sensitive_variables": ["A"]}

    def test_serialize_config_emits_agent_tools_only_when_set(self) -> None:
        payload = build_default_config("demo")
        payload["agents"] = {"copilot": {}}
        unset = serialize_config(DovoConfig.model_validate(payload))
        payload["agents"] = {"copilot": {"tools": {"allow_all": True}}}
        configured = serialize_config(DovoConfig.model_validate(payload))

        assert "tools" not in unset["agents"]["copilot"]
        assert configured["agents"]["copilot"]["tools"] == {"allow": [], "deny": [], "allow_all": True}
