from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.config.generator import build_default_config
from dovo.core.config.loader import (
    ConfigLoadStatus,
    clear_config_cache,
    load_config,
)
from dovo.core.config.models import DovoConfig

SCHEMA_VIOLATION_PAYLOADS = [
    pytest.param(
        {"version": 1},
        ("Config schema validation failed:\n- (root): 'project' is a required property"),
        id="missing_sections",
    ),
    pytest.param(
        {
            "version": 1,
            "project": {"name": "test-project"},
            "worktree": {"max_active_worktrees": "five"},
        },
        ("Config schema validation failed:\n- worktree.max_active_worktrees: 'five' is not of type 'integer'"),
        id="invalid_types",
    ),
    pytest.param(
        {
            **build_default_config("test-project"),
            "version": 99,
        },
        "Config schema validation failed:\n- version: 1 was expected",
        id="unsupported_version",
    ),
]


class ConfigLoaderTests:
    """Integration tests verifying config.json loading and schema parsing contracts."""

    def test_load_returns_strongly_typed_dovo_config(self, workspace_paths: WorkspacePaths) -> None:
        config_path = workspace_paths.config_file
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.OK
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config == DovoConfig.model_validate(payload)
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []

    def test_load_missing_config_returns_failure_result(self, workspace_paths: WorkspacePaths) -> None:
        config_path = workspace_paths.config_file

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.NOT_FOUND
        assert result.config_path == config_path
        assert result.raw is None
        assert result.config is None
        assert result.errors == [f"Configuration file not found at '{config_path}'."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")
        ]

    def test_load_config_defaults_ignore_global_root_error_to_false(self, workspace_paths: WorkspacePaths) -> None:
        config_path = workspace_paths.config_file
        legacy_payload = build_default_config("demo-workspace")
        legacy_payload.pop("ignore_global_root_error")
        Filesystem.atomic_write_json(config_path, legacy_payload)

        result = load_config(workspace_paths)
        generated_payload = build_default_config("demo-workspace")

        assert result.status == ConfigLoadStatus.OK
        assert result.config is not None
        assert result.config.ignore_global_root_error is False
        assert generated_payload["ignore_global_root_error"] is False

    def test_load_config_accepts_boolean_ignore_global_root_error(self, workspace_paths: WorkspacePaths) -> None:
        config_path = workspace_paths.config_file
        enabled_payload = build_default_config("demo-workspace")
        enabled_payload["ignore_global_root_error"] = True
        Filesystem.atomic_write_json(config_path, enabled_payload)

        enabled_result = load_config(workspace_paths)

        invalid_payload = build_default_config("demo-workspace")
        invalid_payload["ignore_global_root_error"] = "true"
        Filesystem.atomic_write_json(config_path, invalid_payload)
        invalid_result = load_config(workspace_paths, bypass_cache=True)

        assert enabled_result.status == ConfigLoadStatus.OK
        assert enabled_result.config is not None
        assert enabled_result.config.ignore_global_root_error is True
        assert invalid_result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "is not of type 'boolean'" in invalid_result.errors[0]

    @pytest.mark.parametrize(("payload", "expected_error"), SCHEMA_VIOLATION_PAYLOADS)
    def test_load_schema_violation_returns_validation_errors(
        self,
        workspace_paths: WorkspacePaths,
        payload: dict[str, Any],
        expected_error: str,
    ) -> None:
        config_path = workspace_paths.config_file
        Filesystem.atomic_write_json(config_path, payload)

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert result.config_path == config_path
        assert result.raw == payload
        assert result.config is None
        assert result.errors == [expected_error]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ]

    def test_load_malformed_json_returns_malformed_json_status_with_location_detail(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        config_path = workspace_paths.config_file
        config_path.write_text("{not valid json", encoding="utf-8")

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.MALFORMED_JSON
        assert result.raw is None
        assert result.config is None
        assert f"Malformed config.json at '{config_path}'" in result.errors[0]
        assert result.remediations == [Remediation(message="Repair JSON syntax, or restore from backup")]


class LoadConfigTests:
    """Schema contract for the default_agent and per-provider agents keys."""

    def test_load_config_rejects_legacy_agent_key(self, workspace_paths: WorkspacePaths) -> None:
        """Return the unexpected-property error for a top-level agent key and leave the file bytes unchanged."""
        payload = {**build_default_config("test-project"), "agent": {"provider": "copilot"}}
        Filesystem.atomic_write_json(workspace_paths.config_file, payload)
        before = workspace_paths.config_file.read_bytes()

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "Additional properties are not allowed ('agent' was unexpected)" in result.errors[0]
        assert workspace_paths.config_file.read_bytes() == before

    def test_load_config_rejects_unregistered_agents_key(self, workspace_paths: WorkspacePaths) -> None:
        """Name the gemini key and the registered tokens copilot and claude."""
        payload = {**build_default_config("test-project"), "agents": {"gemini": {}}}
        Filesystem.atomic_write_json(workspace_paths.config_file, payload)

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "agents: 'gemini' is not one of ['copilot', 'claude']" in result.errors[0]

    @pytest.mark.parametrize(
        "field",
        [
            pytest.param("models", id="legacy-models"),
            pytest.param("provider", id="legacy-provider"),
            pytest.param("bogus", id="unknown"),
        ],
    )
    def test_load_config_rejects_unknown_provider_block_field(
        self, workspace_paths: WorkspacePaths, field: str
    ) -> None:
        """Report the field as an unexpected property of agents.copilot."""
        payload = {**build_default_config("test-project"), "agents": {"copilot": {field: "x"}}}
        Filesystem.atomic_write_json(workspace_paths.config_file, payload)

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert f"agents.copilot: Additional properties are not allowed ('{field}' was unexpected)" in result.errors[0]

    def test_load_config_defaults_default_agent_and_agents(self, workspace_paths: WorkspacePaths) -> None:
        """Load a config without either key as default_agent "copilot" and an empty agents map."""
        Filesystem.atomic_write_json(workspace_paths.config_file, {"version": 1, "project": {"name": "test-project"}})
        clear_config_cache()

        result = load_config(workspace_paths)

        assert result.config is not None
        assert (result.config.default_agent, result.config.agents) == ("copilot", {})


class ConfigEnvironmentSectionTests:
    @staticmethod
    def _write_payload(workspace_paths: WorkspacePaths, environment: object | None) -> None:
        payload = build_default_config("demo-workspace")
        payload.pop("environment")
        if environment is not None:
            payload["environment"] = environment
        Filesystem.atomic_write_json(workspace_paths.config_file, payload)

    def test_load_defaults_sensitive_variables_to_empty_list_when_section_absent(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        self._write_payload(workspace_paths, None)

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.OK
        assert result.config is not None
        assert result.config.environment.sensitive_variables == []

    def test_load_returns_listed_names_and_accepts_duplicates(self, workspace_paths: WorkspacePaths) -> None:
        names = ["AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID", "DATABASE_URL"]
        self._write_payload(workspace_paths, {"sensitive_variables": names})

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.OK
        assert result.config is not None
        assert result.config.environment.sensitive_variables == names

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("A", id="not_a_list"),
            pytest.param(["A", 5], id="non_string_entry"),
            pytest.param({"a": 1}, id="object"),
        ],
    )
    def test_load_rejects_non_list_or_non_string_entry(self, workspace_paths: WorkspacePaths, value: object) -> None:
        self._write_payload(workspace_paths, {"sensitive_variables": value})

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "environment.sensitive_variables" in result.errors[0]

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param("API-KEY", id="dash"),
            pytest.param("TOKEN=abc", id="assignment"),
            pytest.param("has space", id="whitespace"),
            pytest.param("1ABC", id="leading_digit"),
            pytest.param("", id="empty"),
        ],
    )
    def test_load_rejects_bad_pattern_entry_by_index_without_echo(
        self, workspace_paths: WorkspacePaths, entry: str
    ) -> None:
        self._write_payload(workspace_paths, {"sensitive_variables": ["GOOD", entry]})

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "environment.sensitive_variables[1]" in result.errors[0]
        assert f"'{entry}'" not in result.errors[0]

    def test_load_does_not_echo_trailing_newline_entry_that_passes_the_json_schema(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        self._write_payload(workspace_paths, {"sensitive_variables": ["GOOD", "SOMEVALUE\n"]})

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "environment.sensitive_variables[1]" in result.errors[0]
        assert "SOMEVALUE" not in result.errors[0]

    def test_load_rejects_unknown_key_under_environment(self, workspace_paths: WorkspacePaths) -> None:
        self._write_payload(workspace_paths, {"other": []})

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.SCHEMA_INVALID
        assert "environment: Additional properties are not allowed" in result.errors[0]


class ClearConfigCacheTests:
    def test_clearing_specific_path_forces_a_disk_reread_reflecting_the_new_content(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        config_path = workspace_paths.config_file
        Filesystem.atomic_write_json(config_path, build_default_config("first-name"))
        first = load_config(workspace_paths)
        assert first.config is not None
        assert first.config.project.name == "first-name"

        Filesystem.atomic_write_json(config_path, build_default_config("second-name"))
        clear_config_cache(config_path)
        second = load_config(workspace_paths)

        assert second.config is not None
        assert second.config.project.name == "second-name"

    def test_clearing_with_no_path_clears_every_cached_entry(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        workspace_a = workspace_paths_factory(tmp_path / "a", None)
        workspace_b = workspace_paths_factory(tmp_path / "b", None)
        for paths, name in ((workspace_a, "workspace-a"), (workspace_b, "workspace-b")):
            Filesystem.atomic_write_json(paths.config_file, build_default_config(name))
            load_config(paths)

        Filesystem.atomic_write_json(workspace_a.config_file, build_default_config("renamed-a"))
        clear_config_cache()
        reloaded = load_config(workspace_a)

        assert reloaded.config is not None
        assert reloaded.config.project.name == "renamed-a"
