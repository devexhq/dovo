from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem, WorkspacePaths
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
        ("Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- (root): 'project' is a required property"),
        id="missing_sections",
    ),
    pytest.param(
        {
            "version": 1,
            "project": {"name": "test-project"},
            "worktree": {"max_active_worktrees": "five"},
        },
        (
            "Config schema validation failed (CONFIG_SCHEMA_INVALID):\n"
            "- worktree.max_active_worktrees: 'five' is not of type 'integer'"
        ),
        id="invalid_types",
    ),
    pytest.param(
        {
            **build_default_config("test-project"),
            "version": 99,
        },
        "Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- version: 1 was expected",
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
        assert result.fixes == []

    def test_load_missing_config_returns_failure_result(self, workspace_paths: WorkspacePaths) -> None:
        config_path = workspace_paths.config_file

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.NOT_FOUND
        assert result.config_path == config_path
        assert result.raw is None
        assert result.config is None
        assert result.errors == [f"Configuration file not found at '{config_path}' (CONFIG_NOT_FOUND)."]
        assert result.warnings == []
        assert result.fixes == ["Run `dovo init` to create `.dovo/config.json`"]

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
        assert result.fixes == [
            "Run `dovo config validate` for details",
            "Or `dovo init --repair` to insert missing keys without overwriting values",
        ]

    def test_load_malformed_json_returns_malformed_json_status_with_location_detail(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] load_config: config.json containing invalid JSON syntax -> MALFORMED_JSON status, error names the file and a line/column location, config/raw are None."""
        config_path = workspace_paths.config_file
        config_path.write_text("{not valid json", encoding="utf-8")

        result = load_config(workspace_paths)

        assert result.status == ConfigLoadStatus.MALFORMED_JSON
        assert result.raw is None
        assert result.config is None
        assert f"Malformed config.json at '{config_path}'" in result.errors[0]
        assert result.fixes == ["Repair JSON syntax, or restore from backup"]


class ClearConfigCacheTests:
    """[tier-1/unit] clear_config_cache: in-memory load_config cache invalidation."""

    def test_clearing_specific_path_forces_a_disk_reread_reflecting_the_new_content(
        self, workspace_paths: WorkspacePaths
    ) -> None:
        """[tier-1/unit] clear_config_cache(config_path): a subsequent load_config call re-reads the file from disk instead of returning the stale cached result."""
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
        """[tier-1/unit] clear_config_cache(None): clears the entire cache, so a subsequent load for any previously cached workspace re-reads from disk."""
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
