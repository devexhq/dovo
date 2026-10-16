"""Contract tests for default config generation."""

from __future__ import annotations

import json
from pathlib import Path

from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.generator import build_default_config, generate_default_config


class BuildDefaultConfigTests:
    def test_build_default_config_contains_default_agent_and_empty_agents(self) -> None:
        """Return default_agent "copilot", agents {}, and no agent key."""
        payload = build_default_config("demo")

        assert (payload["default_agent"], payload["agents"], "agent" in payload) == ("copilot", {}, False)
        assert CONFIG_VALIDATOR.validate(payload).ok


class GenerateDefaultConfigRepairTests:
    def test_repair_inserts_agent_keys_into_a_config_written_before_them(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        generate_default_config(config_path, "demo")
        legacy = json.loads(config_path.read_text(encoding="utf-8"))
        del legacy["default_agent"], legacy["agents"]
        config_path.write_text(json.dumps(legacy), encoding="utf-8")

        generate_default_config(config_path, "demo", repair=True)

        repaired = json.loads(config_path.read_text(encoding="utf-8"))
        assert (repaired["default_agent"], repaired["agents"]) == ("copilot", {})
        assert CONFIG_VALIDATOR.validate(repaired).ok
