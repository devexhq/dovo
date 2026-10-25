"""Contract tests for default config generation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dovo.common.models import Remediation
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


class GenerateDefaultConfigRemediationTests:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_unwritable_parent_returns_write_permission_remediation(self, tmp_path: Path) -> None:
        """Return the not-writable error with the write-permissions remediation naming the parent directory."""
        parent = tmp_path / "locked"
        parent.mkdir()
        parent.chmod(0o500)
        try:
            result = generate_default_config(parent / "config.json", "demo")
        finally:
            parent.chmod(0o700)

        assert result.errors == [f"CONFIG_PATH_NOT_WRITABLE at {(parent / 'config.json').resolve().as_posix()}"]
        assert result.remediations == [
            Remediation(message=f"Ensure write permissions for '{parent.resolve().as_posix()}' directory")
        ]

    def test_repair_of_invalid_json_returns_manual_and_overwrite_remediations(self, tmp_path: Path) -> None:
        """Return the invalid-JSON error with the manual-repair and 'dovo init --overwrite' remediations."""
        config_path = tmp_path / "config.json"
        config_path.write_text("{bad json", encoding="utf-8")

        result = generate_default_config(config_path, "demo", repair=True)

        assert result.remediations == [
            Remediation(message="Repair invalid JSON manually"),
            Remediation(message="Run 'dovo init --overwrite'", command="dovo init --overwrite"),
        ]
