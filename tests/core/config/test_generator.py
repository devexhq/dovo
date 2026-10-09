"""Contract tests for default config generation."""

from __future__ import annotations

import json
from pathlib import Path

from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.generator import generate_default_config


class AgentConfigEnvTests:
    def test_default_config_generation_includes_env_defaults(self, tmp_path: Path) -> None:
        """[tier-1/integration] generate_default_config: written payload agent block contains env_passthrough == [] and env_mode == 'allowlist' and passes CONFIG_VALIDATOR."""
        config_path = tmp_path / "config.json"

        result = generate_default_config(config_path, "demo")

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        assert result.ok
        assert (payload["agent"]["env_passthrough"], payload["agent"]["env_mode"]) == ([], "allowlist")
        assert CONFIG_VALIDATOR.validate(payload).ok

    def test_repair_inserts_env_defaults_into_a_config_written_before_them(self, tmp_path: Path) -> None:
        """[tier-1/integration] generate_default_config: repair=True on a config lacking both agent env keys inserts them and the result passes CONFIG_VALIDATOR."""
        config_path = tmp_path / "config.json"
        generate_default_config(config_path, "demo")
        legacy = json.loads(config_path.read_text(encoding="utf-8"))
        del legacy["agent"]["env_passthrough"], legacy["agent"]["env_mode"]
        config_path.write_text(json.dumps(legacy), encoding="utf-8")

        generate_default_config(config_path, "demo", repair=True)

        repaired = json.loads(config_path.read_text(encoding="utf-8"))
        assert (repaired["agent"]["env_passthrough"], repaired["agent"]["env_mode"]) == ([], "allowlist")
        assert CONFIG_VALIDATOR.validate(repaired).ok
