"""Contract tests for default config generation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.models import Remediation
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.generator import build_default_config, generate_default_config


class BuildDefaultConfigTests:
    def test_build_default_config_contains_default_agent_and_empty_agents(self) -> None:
        """Return default_agent "copilot", agents {}, and no agent key."""
        payload = build_default_config("demo")

        assert (payload["default_agent"], payload["agents"], "agent" in payload) == ("copilot", {}, False)
        assert CONFIG_VALIDATOR.validate(payload).ok


class GenerateDefaultConfigTests:
    @pytest.mark.parametrize(
        ("existing_name", "project_name", "overwrite", "expected_flags", "expected_name"),
        [
            pytest.param(None, "demo", False, (True, False, False), "demo", id="create"),
            pytest.param("original-name", "new-name", False, (False, True, False), "original-name", id="skip-existing"),
            pytest.param("original-name", "replaced-name", True, (False, False, True), "replaced-name", id="overwrite"),
        ],
    )
    def test_policy_writes_or_skips_config(
        self,
        workspace_paths: WorkspacePaths,
        existing_name: str | None,
        project_name: str,
        overwrite: bool,
        expected_flags: tuple[bool, bool, bool],
        expected_name: str,
    ) -> None:
        """Create a missing config, skip an existing one, or replace it with overwrite."""
        if existing_name is not None:
            Filesystem.atomic_write_json(workspace_paths.config_file, build_default_config(existing_name))

        result = generate_default_config(workspace_paths.config_file, project_name, overwrite=overwrite)

        assert (result.created, result.skipped_existing, result.overwritten) == expected_flags
        written = json.loads(workspace_paths.config_file.read_text(encoding="utf-8"))
        assert written["project"]["name"] == expected_name


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

        assert result.errors == [f"Config path '{(parent / 'config.json').resolve().as_posix()}' is not writable."]
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
