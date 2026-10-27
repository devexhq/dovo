"""Integration tests verifying config mutation, atomic updates, and type casting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dovo.common.filesystem import Filesystem
from dovo.common.models import Remediation
from dovo.core.config.generator import build_default_config
from dovo.core.config.mutate import (
    ConfigSetStatus,
    ConfigUnsetStatus,
    set_config_value_result,
    unset_config_value_result,
    unset_nested_value,
)
from dovo.core.config.validate import ConfigValidationStatus, validate_config_result


class ConfigMutationTests:
    """Integration tests verifying config mutation, atomic updates, and type casting."""

    def test_set_agents_model_creates_intermediate_objects(self, isolated_workspace: Path) -> None:
        """Write {"agents": {"copilot": {"model": "m"}}} into a config without agents and keep default_agent."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        del payload["agents"]
        Filesystem.atomic_write_json(config_path, payload)

        result = set_config_value_result("agents.copilot.model", "m", config_path=config_path)

        assert result.status == ConfigSetStatus.OK
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.value == "m"
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []

        data = json.loads(config_path.read_text())
        assert data["agents"] == {"copilot": {"model": "m"}}
        assert data["default_agent"] == payload["default_agent"]

    def test_set_default_agent_rejects_unregistered_token(self, isolated_workspace: Path) -> None:
        """Return SCHEMA_INVALID naming the registered tokens and leave the file bytes unchanged for "gemini"."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))
        before = config_path.read_bytes()

        result = set_config_value_result("default_agent", "gemini", config_path=config_path)

        assert result.status == ConfigSetStatus.SCHEMA_INVALID
        assert result.errors == [
            "Config schema validation failed:\n- default_agent: 'gemini' is not one of ['copilot', 'claude']"
        ]
        assert config_path.read_bytes() == before

    def test_set_dot_path_converts_and_validates_types(self, isolated_workspace: Path) -> None:
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
        assert bool_result.remediations == []
        assert json.loads(config_path.read_text())["telemetry"]["enabled"] is True

        schema_error_result = set_config_value_result(
            "worktree.max_active_worktrees", "not_an_int", config_path=config_path
        )
        assert schema_error_result.status == ConfigSetStatus.SCHEMA_INVALID
        assert schema_error_result.config_path == config_path
        assert schema_error_result.key == "worktree.max_active_worktrees"
        assert schema_error_result.value == "not_an_int"
        assert schema_error_result.errors == [
            "Config schema validation failed:\n- worktree.max_active_worktrees: 'not_an_int' is not of type 'integer'"
        ]
        assert schema_error_result.remediations == [
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ]
        assert schema_error_result.warnings == []
        assert json.loads(config_path.read_text())["worktree"]["max_active_worktrees"] == 3

        disk_data = json.loads(config_path.read_text())
        disk_data["agents"] = "scalar"
        Filesystem.atomic_write_json(config_path, disk_data)

        collision_result = set_config_value_result("agents.copilot.model", "qwen2.5-coder", config_path=config_path)
        assert collision_result.status == ConfigSetStatus.TYPE_COLLISION
        assert collision_result.config_path == config_path
        assert collision_result.key == "agents.copilot.model"
        assert collision_result.value == "qwen2.5-coder"
        assert collision_result.errors == [
            "Cannot set 'agents.copilot.model'. 'agents' is already defined as a scalar value."
        ]
        assert collision_result.warnings == []
        assert collision_result.remediations == []

    @pytest.mark.parametrize(
        ("key", "raw_value", "expected"),
        [
            pytest.param("worktree.base_ref", '"12345"', "12345", id="quoted-numeric"),
            pytest.param("worktree.max_active_worktrees", "5", 5, id="unquoted-int"),
        ],
    )
    def test_set_stores_single_coercion_of_raw_value(
        self, isolated_workspace: Path, key: str, raw_value: str, expected: object
    ) -> None:
        """Return OK with value == expected (same type) and write exactly that JSON value at key."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))

        result = set_config_value_result(key, raw_value, config_path=config_path)

        assert result.status == ConfigSetStatus.OK
        assert result.errors == []
        assert result.value == expected
        assert type(result.value) is type(expected)
        section, field = key.split(".")
        stored = json.loads(config_path.read_text())[section][field]
        assert stored == expected
        assert type(stored) is type(expected)

    def test_set_unquoted_numeric_for_string_key_returns_schema_invalid(self, isolated_workspace: Path) -> None:
        """Return SCHEMA_INVALID with value 12345 and the base_ref type error, leaving file bytes unchanged."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))
        before = config_path.read_bytes()

        result = set_config_value_result("worktree.base_ref", "12345", config_path=config_path)

        assert result.status == ConfigSetStatus.SCHEMA_INVALID
        assert result.value == 12345
        assert type(result.value) is int
        assert result.errors == ["Config schema validation failed:\n- worktree.base_ref: 12345 is not of type 'string'"]
        assert config_path.read_bytes() == before


class ConfigUnsetNestedValueTests:
    """Direct unit tests for unset_nested_value's dot-path removal semantics."""

    def test_removes_existing_nested_leaf_returns_true(self) -> None:
        config_dict: dict[str, Any] = {"agents": {"copilot": {"model": "x", "endpoint": "y"}}}

        result = unset_nested_value(config_dict, "agents.copilot.model")

        assert result is True
        assert config_dict == {"agents": {"copilot": {"endpoint": "y"}}}

    def test_removes_entire_top_level_section_returns_true(self) -> None:
        config_dict: dict[str, Any] = {"agents": {"copilot": {"model": "x"}}, "worktree": {"max_active_worktrees": 3}}

        result = unset_nested_value(config_dict, "agents")

        assert result is True
        assert config_dict == {"worktree": {"max_active_worktrees": 3}}

    @pytest.mark.parametrize(
        "config_dict, dot_path",
        [
            pytest.param({"agents": {}}, "telemetry.enabled", id="missing_top_level"),
            pytest.param({"agents": {}}, "agents.copilot.model", id="missing_nested_parent"),
            pytest.param(
                {"agents": {"copilot": {"endpoint": "x"}}}, "agents.copilot.model", id="present_parent_missing_leaf"
            ),
        ],
    )
    def test_missing_key_is_noop_returns_false(self, config_dict: dict[str, Any], dot_path: str) -> None:
        before = json.loads(json.dumps(config_dict))

        result = unset_nested_value(config_dict, dot_path)

        assert result is False
        assert config_dict == before

    @pytest.mark.parametrize(
        "dot_path",
        [pytest.param("", id="empty"), pytest.param("   ", id="whitespace")],
    )
    def test_empty_or_blank_path_raises_value_error(self, dot_path: str) -> None:
        with pytest.raises(ValueError, match=r"Cannot unset '': config key path must be a non-empty dot path\."):
            unset_nested_value({}, dot_path)

    def test_empty_segment_raises_value_error(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"Cannot unset 'agents\.\.model': config key path contains an empty segment\.",
        ):
            unset_nested_value({}, "agents..model")

    def test_scalar_traversal_collision_raises_value_error(self) -> None:
        config_dict: dict[str, Any] = {"agents": "scalar"}

        with pytest.raises(
            ValueError,
            match=r"Cannot unset 'agents\.copilot\.model'\. 'agents' is already defined as a scalar value\.",
        ):
            unset_nested_value(config_dict, "agents.copilot.model")

        assert config_dict == {"agents": "scalar"}


class ConfigUnsetMutationTests:
    """Integration tests verifying config unset mutation, atomic updates, and no-op safety."""

    def test_unset_agents_model_keeps_emptied_provider_block(self, isolated_workspace: Path) -> None:
        """Leave "copilot": {} under agents after removing its only key, and the file still validates."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agents"] = {"copilot": {"model": "gpt-4.1"}}
        Filesystem.atomic_write_json(config_path, payload)

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.OK
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is True
        assert result.previous_value == "gpt-4.1"
        assert result.errors == []
        assert result.warnings == []
        assert result.remediations == []
        data = json.loads(config_path.read_text())
        assert data["agents"] == {"copilot": {}}
        assert validate_config_result(config_path).status == ConfigValidationStatus.VALID

    def test_unset_missing_key_returns_ok_without_write(self, isolated_workspace: Path) -> None:
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
        assert result.remediations == []
        assert config_path.read_bytes() == before

    def test_unset_missing_config_returns_not_found(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.NOT_FOUND
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [f"Configuration file not found at '{config_path}'."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")
        ]

    def test_unset_config_path_is_directory_returns_path_is_directory(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.mkdir(parents=True)

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.PATH_IS_DIRECTORY
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [f"Config path is a directory, not a file: '{config_path}'."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Remove the directory or point config_path at a file")]

    def test_unset_malformed_json_returns_malformed_json(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.write_text("{not valid json", encoding="utf-8")

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.MALFORMED_JSON
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [
            f"Malformed config.json at '{config_path}': "
            "Expecting property name enclosed in double quotes at line 1 column 2 (char 1)."
        ]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Repair JSON syntax, or restore from backup")]

    def test_unset_root_not_object_returns_root_not_object(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        config_path.write_text("[]", encoding="utf-8")

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.ROOT_NOT_OBJECT
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == [f"Malformed config.json at '{config_path}': root must be an object."]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Ensure 'config.json' is a JSON object, not an array or scalar")
        ]

    def test_unset_schema_invalid_removal_rejected_without_write(self, isolated_workspace: Path) -> None:
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
        assert result.errors == ["Config schema validation failed:\n- (root): 'project' is a required property"]
        assert result.warnings == []
        assert result.remediations == [
            Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
            Remediation(
                message="Run 'dovo init --repair' to insert missing keys without overwriting values",
                command="dovo init --repair",
            ),
        ]
        assert config_path.read_bytes() == before

    def test_unset_write_failure_returns_write_failed(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agents"] = {"copilot": {"model": "gpt-4.1"}}
        Filesystem.atomic_write_json(config_path, payload)

        def _raise_os_error(path: Path, data: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Filesystem, "atomic_write_json", staticmethod(_raise_os_error))

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.WRITE_FAILED
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is True
        assert result.previous_value == "gpt-4.1"
        assert result.errors == [f"Unable to write config.json at '{config_path}': disk full."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Check file permissions and free disk space")]

    def test_unset_empty_path_returns_invalid_path(self, isolated_workspace: Path) -> None:
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
        assert result.remediations == []

    def test_unset_type_collision_returns_type_collision(self, isolated_workspace: Path) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agents"] = "scalar"
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.TYPE_COLLISION
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is False
        assert result.previous_value is None
        assert result.errors == ["Cannot unset 'agents.copilot.model'. 'agents' is already defined as a scalar value."]
        assert result.warnings == []
        assert result.remediations == []
        assert config_path.read_bytes() == before
