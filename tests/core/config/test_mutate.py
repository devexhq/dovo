"""Integration tests verifying config mutation, atomic updates, and type casting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem
from dovo.core.config.generator import build_default_config
from dovo.core.config.mutate import (
    ConfigSetStatus,
    ConfigUnsetStatus,
    set_config_value_result,
    unset_config_value_result,
    unset_nested_value,
)


class ConfigMutationTests:
    """Integration tests verifying config mutation, atomic updates, and type casting."""

    def test_set_dot_path_updates_scalar_value(self, isolated_workspace: Path) -> None:
        """Verify dot-path sets nested scalar value atomically and preserves siblings."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        result = set_config_value_result("agent.model", "qwen2.5-coder", config_path=config_path)

        assert result.status == ConfigSetStatus.OK
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.value == "qwen2.5-coder"
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []

        data = json.loads(config_path.read_text())
        assert data["agent"]["model"] == "qwen2.5-coder"
        assert data["agent"]["provider"] == payload["agent"]["provider"]

    def test_set_dot_path_converts_and_validates_types(self, isolated_workspace: Path) -> None:
        """Verify string "true" is converted to boolean True and invalid types are rejected."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        bool_result = set_config_value_result("telemetry.enabled", "true", config_path=config_path)
        assert bool_result.status == ConfigSetStatus.OK
        assert bool_result.config_path == config_path
        assert bool_result.key == "telemetry.enabled"
        assert bool_result.value is True
        assert bool_result.errors == []
        assert bool_result.warnings == []
        assert bool_result.fixes == []
        assert json.loads(config_path.read_text())["telemetry"]["enabled"] is True

        schema_error_result = set_config_value_result(
            "worktree.max_active_worktrees", "not_an_int", config_path=config_path
        )
        assert schema_error_result.status == ConfigSetStatus.SCHEMA_INVALID
        assert schema_error_result.config_path == config_path
        assert schema_error_result.key == "worktree.max_active_worktrees"
        assert schema_error_result.value == "not_an_int"
        assert schema_error_result.errors == [
            "Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- worktree.max_active_worktrees: 'not_an_int' is not of type 'integer'"
        ]
        assert schema_error_result.fixes == [
            "Run `dovo config validate` for details",
            "Or `dovo init --repair` to insert missing keys without overwriting values",
        ]
        assert schema_error_result.warnings == []
        assert json.loads(config_path.read_text())["worktree"]["max_active_worktrees"] == 3

        disk_data = json.loads(config_path.read_text())
        disk_data["agent"] = "scalar"
        Filesystem.atomic_write_json(config_path, disk_data)

        collision_result = set_config_value_result("agent.model", "qwen2.5-coder", config_path=config_path)
        assert collision_result.status == ConfigSetStatus.TYPE_COLLISION
        assert collision_result.config_path == config_path
        assert collision_result.key == "agent.model"
        assert collision_result.value == "qwen2.5-coder"
        assert collision_result.errors == ["Cannot set 'agent.model'. 'agent' is already defined as a scalar value."]
        assert collision_result.warnings == []
        assert collision_result.fixes == []


class ConfigUnsetNestedValueTests:
    """Direct unit tests for unset_nested_value's dot-path removal semantics."""

    def test_removes_existing_nested_leaf_returns_true(self) -> None:
        """[tier-1/domain] unset_nested_value: removing an existing nested leaf returns True and deletes only that key."""
        config_dict: dict[str, Any] = {"agent": {"model": "x", "provider": "y"}}

        result = unset_nested_value(config_dict, "agent.model")

        assert result is True
        assert config_dict == {"agent": {"provider": "y"}}

    def test_removes_entire_top_level_section_returns_true(self) -> None:
        """[tier-1/domain] unset_nested_value: removing a single-segment top-level key returns True and deletes the whole section."""
        config_dict: dict[str, Any] = {"agent": {"model": "x"}, "worktree": {"max_active_worktrees": 3}}

        result = unset_nested_value(config_dict, "agent")

        assert result is True
        assert config_dict == {"worktree": {"max_active_worktrees": 3}}

    @pytest.mark.parametrize(
        "config_dict, dot_path",
        [
            pytest.param({"agent": {}}, "telemetry.enabled", id="missing_top_level"),
            pytest.param({"agent": {}}, "agent.nested.deep", id="missing_nested_parent"),
            pytest.param({"agent": {"provider": "x"}}, "agent.model", id="present_parent_missing_leaf"),
        ],
    )
    def test_missing_key_is_noop_returns_false(self, config_dict: dict[str, Any], dot_path: str) -> None:
        """[tier-1/domain] unset_nested_value: an absent parent or final segment returns False and leaves config_dict unmutated."""
        before = json.loads(json.dumps(config_dict))

        result = unset_nested_value(config_dict, dot_path)

        assert result is False
        assert config_dict == before

    @pytest.mark.parametrize(
        "dot_path",
        [pytest.param("", id="empty"), pytest.param("   ", id="whitespace")],
    )
    def test_empty_or_blank_path_raises_value_error(self, dot_path: str) -> None:
        """[tier-1/domain] unset_nested_value: an empty or all-whitespace dot_path raises ValueError with the non-empty-path message."""
        with pytest.raises(ValueError, match=r"Cannot unset '': config key path must be a non-empty dot path\."):
            unset_nested_value({}, dot_path)

    def test_empty_segment_raises_value_error(self) -> None:
        """[tier-1/domain] unset_nested_value: a dot_path containing an empty segment ('agent..model') raises ValueError naming the empty-segment defect."""
        with pytest.raises(
            ValueError,
            match=r"Cannot unset 'agent\.\.model': config key path contains an empty segment\.",
        ):
            unset_nested_value({}, "agent..model")

    def test_scalar_traversal_collision_raises_value_error(self) -> None:
        """[tier-1/domain] unset_nested_value: traversing through a scalar-valued intermediate key raises ValueError naming the conflicting segment, without mutating config_dict."""
        config_dict: dict[str, Any] = {"agent": "scalar"}

        with pytest.raises(
            ValueError,
            match=r"Cannot unset 'agent\.model'\. 'agent' is already defined as a scalar value\.",
        ):
            unset_nested_value(config_dict, "agent.model")

        assert config_dict == {"agent": "scalar"}


class ConfigUnsetMutationTests:
    """Integration tests verifying config unset mutation, atomic updates, and no-op safety."""

    def test_unset_dot_path_removes_scalar_value_and_persists(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: removing an existing nested leaf returns OK with existed=True and previous_value set, and persists the removal to disk."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)
        previous_model = payload["agent"]["model"]

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.OK
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is True
        assert result.previous_value == previous_model
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        data = json.loads(config_path.read_text())
        assert "model" not in data["agent"]
        assert data["agent"]["provider"] == payload["agent"]["provider"]

    def test_unset_leaves_empty_parent_object_after_removing_all_children(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: removing every child key of a section leaves that section's JSON object present and empty, not deleted."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        for child_key in ("provider", "model", "endpoint", "temperature", "max_tokens", "env_passthrough", "env_mode"):
            result = unset_config_value_result(f"agent.{child_key}", config_path=config_path)
            assert result.ok

        data = json.loads(config_path.read_text())
        assert data["agent"] == {}

    def test_unset_missing_key_returns_ok_without_write(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: unsetting a key absent from config.json returns OK with existed=False and performs no disk write."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()

        result = unset_config_value_result("telemetry.nonexistent", config_path=config_path)

        assert result.status == ConfigUnsetStatus.OK
        assert result.config_path == config_path
        assert result.key == "telemetry.nonexistent"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == []
        assert result.warnings == []
        assert result.fixes == []
        assert config_path.read_bytes() == before

    def test_unset_missing_config_returns_not_found(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: a missing config.json returns ConfigUnsetStatus.NOT_FOUND."""
        config_path = isolated_workspace / ".dovo" / "config.json"

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.NOT_FOUND
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [f"Configuration file not found at '{config_path}' (CONFIG_NOT_FOUND)."]
        assert result.warnings == []
        assert result.fixes == ["Run `dovo init` to create `.dovo/config.json`"]

    def test_unset_config_path_is_directory_returns_path_is_directory(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: config.json existing as a directory returns ConfigUnsetStatus.PATH_IS_DIRECTORY."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.mkdir(parents=True)

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.PATH_IS_DIRECTORY
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [f"Config path is a directory, not a file: '{config_path}' (CONFIG_PATH_IS_DIRECTORY)."]
        assert result.warnings == []
        assert result.fixes == ["Remove the directory or point config_path at a file"]

    def test_unset_malformed_json_returns_malformed_json(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: invalid JSON text returns ConfigUnsetStatus.MALFORMED_JSON."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.write_text("{not valid json", encoding="utf-8")

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.MALFORMED_JSON
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [
            f"Malformed config.json at '{config_path}': "
            "Expecting property name enclosed in double quotes at line 1 column 2 (char 1) "
            "(CONFIG_MALFORMED_JSON)."
        ]
        assert result.warnings == []
        assert result.fixes == ["Repair JSON syntax, or restore from backup"]

    def test_unset_root_not_object_returns_root_not_object(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: a JSON array root returns ConfigUnsetStatus.ROOT_NOT_OBJECT."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.write_text("[]", encoding="utf-8")

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.ROOT_NOT_OBJECT
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [
            f"Malformed config.json at '{config_path}': root must be an object (CONFIG_ROOT_NOT_OBJECT)."
        ]
        assert result.warnings == []
        assert result.fixes == ["Ensure config.json is a JSON object, not an array or scalar"]

    def test_unset_schema_invalid_removal_rejected_without_write(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: removing the required 'project' key returns ConfigUnsetStatus.SCHEMA_INVALID and leaves config.json unchanged on disk."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()

        result = unset_config_value_result("project", config_path=config_path)

        assert result.status == ConfigUnsetStatus.SCHEMA_INVALID
        assert result.config_path == config_path
        assert result.key == "project"
        assert result.existed is True
        assert result.previous_value == payload["project"]
        assert result.errors == [
            "Config schema validation failed (CONFIG_SCHEMA_INVALID):\n- (root): 'project' is a required property"
        ]
        assert result.warnings == []
        assert result.fixes == [
            "Run `dovo config validate` for details",
            "Or `dovo init --repair` to insert missing keys without overwriting values",
        ]
        assert config_path.read_bytes() == before

    def test_unset_write_failure_returns_write_failed(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/domain] unset_config_value_result: an OSError from Filesystem.atomic_write_json returns ConfigUnsetStatus.WRITE_FAILED."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        def _raise_os_error(path: Path, data: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_json", staticmethod(_raise_os_error))

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.WRITE_FAILED
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is True
        assert result.previous_value == payload["agent"]["model"]
        assert result.errors == [f"Unable to write config.json at '{config_path}': disk full (CONFIG_WRITE_FAILED)."]
        assert result.warnings == []
        assert result.fixes == ["Check file permissions and free disk space"]

    def test_unset_empty_path_returns_invalid_path(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: an empty dot-path key returns ConfigUnsetStatus.INVALID_PATH."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        Filesystem.atomic_write_json(config_path, payload)

        result = unset_config_value_result("", config_path=config_path)

        assert result.status == ConfigUnsetStatus.INVALID_PATH
        assert result.config_path == config_path
        assert result.key == ""
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == ["Cannot unset '': config key path must be a non-empty dot path."]
        assert result.warnings == []
        assert result.fixes == []

    def test_unset_type_collision_returns_type_collision(self, isolated_workspace: Path) -> None:
        """[tier-1/domain] unset_config_value_result: traversing through a scalar-valued intermediate returns ConfigUnsetStatus.TYPE_COLLISION and leaves config.json unchanged."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agent"] = "scalar"
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()

        result = unset_config_value_result("agent.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.TYPE_COLLISION
        assert result.config_path == config_path
        assert result.key == "agent.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == ["Cannot unset 'agent.model'. 'agent' is already defined as a scalar value."]
        assert result.warnings == []
        assert result.fixes == []
        assert config_path.read_bytes() == before
