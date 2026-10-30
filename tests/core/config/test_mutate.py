"""Integration tests verifying config mutation, atomic updates, and type casting."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import Remediation
from dovo.core.config.generator import build_default_config
from dovo.core.config.mutate import (
    ConfigSetStatus,
    ConfigUnsetResult,
    ConfigUnsetStatus,
    set_config_value_result,
    unset_config_value_result,
    unset_nested_value,
)
from dovo.core.config.validate import ConfigValidationStatus, validate_config_result
from tests.harness.diagnostic_log import diagnostic_record, read_single_event

NOT_FOUND_REMEDIATIONS = [Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")]
DIRECTORY_REMEDIATIONS = [Remediation(message="Remove the directory or point config_path at a file")]
JSON_REMEDIATIONS = [Remediation(message="Repair JSON syntax, or restore from backup")]
ROOT_REMEDIATIONS = [Remediation(message="Ensure 'config.json' is a JSON object, not an array or scalar")]
SCHEMA_REMEDIATIONS = [
    Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
    Remediation(
        message="Run 'dovo init --repair' to insert missing keys without overwriting values",
        command="dovo init --repair",
    ),
]
MALFORMED_JSON_ERROR = (
    "Malformed config.json at '{path}': Expecting property name enclosed in double quotes at line 1 column 2 (char 1)."
)
SENSITIVE_PATTERN_ERROR = (
    "Config schema validation failed:\n"
    "- environment.sensitive_variables[{index}]: String should match pattern '^[A-Za-z_][A-Za-z0-9_]*$'"
)


def _write_default(config_path: Path) -> None:
    """Write the default config."""
    Filesystem.atomic_write_json(config_path, build_default_config("demo-workspace"))


def _write_scalar_agents(config_path: Path) -> None:
    """Write a default config whose agents section is a scalar."""
    payload = build_default_config("demo-workspace")
    payload["agents"] = "scalar"
    Filesystem.atomic_write_json(config_path, payload)


def _write_trailing_newline_entry(config_path: Path) -> None:
    """Write a schema-valid config whose sensitive_variables entry only DovoConfig rejects."""
    payload = build_default_config("demo-workspace")
    payload["environment"] = {"sensitive_variables": ["GOOD", "SOMEVALUE\n"]}
    Filesystem.atomic_write_json(config_path, payload)


def _snapshot(path: Path) -> bytes | bool:
    """Return the file's bytes, or whether a directory is at path when no file is."""
    return path.read_bytes() if path.is_file() else path.is_dir()


def _deny_config_read(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    """Make reading config_path raise PermissionError('denied'), leaving every other read alone."""
    real_read_text = Path.read_text

    def _denied_read(
        self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
    ) -> str:
        if self == config_path:
            raise PermissionError("denied")
        return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", _denied_read)


def _fail_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every atomic JSON write raise OSError('disk full')."""

    def _raise_os_error(path: Path, data: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Filesystem, "atomic_write_json", staticmethod(_raise_os_error))


SET_FAILURE_CASES = [
    pytest.param(
        lambda path: None,
        "agents.copilot.model",
        "m",
        ConfigSetStatus.NOT_FOUND,
        ErrorCode.CONFIG_NOT_FOUND,
        "Configuration file not found at '{path}'.",
        NOT_FOUND_REMEDIATIONS,
        id="not_found",
    ),
    pytest.param(
        lambda path: path.mkdir(),
        "agents.copilot.model",
        "m",
        ConfigSetStatus.PATH_IS_DIRECTORY,
        ErrorCode.CONFIG_PATH_INVALID,
        "Config path is a directory, not a file: '{path}'.",
        DIRECTORY_REMEDIATIONS,
        id="path_is_directory",
    ),
    pytest.param(
        lambda path: path.write_text("{not valid json", encoding="utf-8"),
        "agents.copilot.model",
        "m",
        ConfigSetStatus.MALFORMED_JSON,
        ErrorCode.CONFIG_JSON_INVALID,
        MALFORMED_JSON_ERROR,
        JSON_REMEDIATIONS,
        id="malformed_json",
    ),
    pytest.param(
        lambda path: path.write_text("[]", encoding="utf-8"),
        "agents.copilot.model",
        "m",
        ConfigSetStatus.ROOT_NOT_OBJECT,
        ErrorCode.CONFIG_ROOT_INVALID,
        "Malformed config.json at '{path}': root must be an object.",
        ROOT_REMEDIATIONS,
        id="root_not_object",
    ),
    pytest.param(
        _write_default,
        "worktree.max_active_worktrees",
        "not_an_int",
        ConfigSetStatus.SCHEMA_INVALID,
        ErrorCode.CONFIG_SCHEMA_INVALID,
        "Config schema validation failed:\n- worktree.max_active_worktrees: 'not_an_int' is not of type 'integer'",
        SCHEMA_REMEDIATIONS,
        id="schema_invalid",
    ),
    pytest.param(
        _write_default,
        "environment.sensitive_variables",
        '["SOMEVALUE\\n"]',
        ConfigSetStatus.SCHEMA_INVALID,
        ErrorCode.CONFIG_SCHEMA_INVALID,
        SENSITIVE_PATTERN_ERROR.format(index=0),
        SCHEMA_REMEDIATIONS,
        id="model_invalid",
    ),
    pytest.param(
        _write_scalar_agents,
        "agents.copilot.model",
        "m",
        ConfigSetStatus.TYPE_COLLISION,
        ErrorCode.CONFIG_TYPE_CONFLICT,
        "Cannot set 'agents.copilot.model'. 'agents' is already defined as a scalar value.",
        [],
        id="type_collision",
    ),
    pytest.param(
        _write_default,
        "",
        "m",
        ConfigSetStatus.INVALID_PATH,
        ErrorCode.CONFIG_KEY_PATH_INVALID,
        "Cannot set '': config key path must be a non-empty dot path.",
        [],
        id="empty_key",
    ),
    pytest.param(
        _write_default,
        "agents..model",
        "m",
        ConfigSetStatus.INVALID_PATH,
        ErrorCode.CONFIG_KEY_PATH_INVALID,
        "Cannot set 'agents..model': config key path contains an empty segment.",
        [],
        id="empty_segment",
    ),
    pytest.param(
        lambda path: None,
        "",
        "m",
        ConfigSetStatus.NOT_FOUND,
        ErrorCode.CONFIG_NOT_FOUND,
        "Configuration file not found at '{path}'.",
        NOT_FOUND_REMEDIATIONS,
        id="empty_key_missing_config",
    ),
]


UNSET_FAILURE_CASES = [
    pytest.param(
        lambda path: None,
        "agents.copilot.model",
        ConfigUnsetStatus.NOT_FOUND,
        ErrorCode.CONFIG_NOT_FOUND,
        "Configuration file not found at '{path}'.",
        NOT_FOUND_REMEDIATIONS,
        False,
        id="not_found",
    ),
    pytest.param(
        lambda path: path.mkdir(),
        "agents.copilot.model",
        ConfigUnsetStatus.PATH_IS_DIRECTORY,
        ErrorCode.CONFIG_PATH_INVALID,
        "Config path is a directory, not a file: '{path}'.",
        DIRECTORY_REMEDIATIONS,
        False,
        id="path_is_directory",
    ),
    pytest.param(
        lambda path: path.write_text("{not valid json", encoding="utf-8"),
        "agents.copilot.model",
        ConfigUnsetStatus.MALFORMED_JSON,
        ErrorCode.CONFIG_JSON_INVALID,
        MALFORMED_JSON_ERROR,
        JSON_REMEDIATIONS,
        False,
        id="malformed_json",
    ),
    pytest.param(
        lambda path: path.write_text("[]", encoding="utf-8"),
        "agents.copilot.model",
        ConfigUnsetStatus.ROOT_NOT_OBJECT,
        ErrorCode.CONFIG_ROOT_INVALID,
        "Malformed config.json at '{path}': root must be an object.",
        ROOT_REMEDIATIONS,
        False,
        id="root_not_object",
    ),
    pytest.param(
        _write_default,
        "project",
        ConfigUnsetStatus.SCHEMA_INVALID,
        ErrorCode.CONFIG_SCHEMA_INVALID,
        "Config schema validation failed:\n- (root): 'project' is a required property",
        SCHEMA_REMEDIATIONS,
        True,
        id="schema_invalid",
    ),
    pytest.param(
        _write_trailing_newline_entry,
        "telemetry",
        ConfigUnsetStatus.SCHEMA_INVALID,
        ErrorCode.CONFIG_SCHEMA_INVALID,
        SENSITIVE_PATTERN_ERROR.format(index=1),
        SCHEMA_REMEDIATIONS,
        True,
        id="model_invalid",
    ),
    pytest.param(
        _write_scalar_agents,
        "agents.copilot.model",
        ConfigUnsetStatus.TYPE_COLLISION,
        ErrorCode.CONFIG_TYPE_CONFLICT,
        "Cannot unset 'agents.copilot.model'. 'agents' is already defined as a scalar value.",
        [],
        False,
        id="type_collision",
    ),
    pytest.param(
        _write_default,
        "",
        ConfigUnsetStatus.INVALID_PATH,
        ErrorCode.CONFIG_KEY_PATH_INVALID,
        "Cannot unset '': config key path must be a non-empty dot path.",
        [],
        False,
        id="empty_key",
    ),
    pytest.param(
        _write_default,
        "agents..model",
        ConfigUnsetStatus.INVALID_PATH,
        ErrorCode.CONFIG_KEY_PATH_INVALID,
        "Cannot unset 'agents..model': config key path contains an empty segment.",
        [],
        False,
        id="empty_segment",
    ),
    pytest.param(
        lambda path: None,
        "",
        ConfigUnsetStatus.NOT_FOUND,
        ErrorCode.CONFIG_NOT_FOUND,
        "Configuration file not found at '{path}'.",
        NOT_FOUND_REMEDIATIONS,
        False,
        id="empty_key_missing_config",
    ),
]


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

    @pytest.mark.parametrize(
        ("write", "key", "raw_value", "status", "error_code", "error", "remediations"), SET_FAILURE_CASES
    )
    def test_set_failure_reports_status_and_error_code(
        self,
        isolated_workspace: Path,
        write: Callable[[Path], object],
        key: str,
        raw_value: str,
        status: ConfigSetStatus,
        error_code: ErrorCode,
        error: str,
        remediations: list[Remediation],
    ) -> None:
        """Report the row's status, error_code, errors and remediations, record nothing, and leave the path unchanged."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        write(config_path)
        before = _snapshot(config_path)

        result = set_config_value_result(key, raw_value, config_path=config_path)

        assert result.status == status
        assert result.error_code == error_code
        assert result.errors == [error.format(path=config_path)]
        assert result.remediations == remediations
        assert result.diagnostics == []
        assert _snapshot(config_path) == before

    def test_set_unreadable_config_reports_config_unreadable_with_one_record(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Report CONFIG_UNREADABLE with one config.read record naming the path."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        _write_default(config_path)
        _deny_config_read(monkeypatch, config_path)

        result = set_config_value_result("agents.copilot.model", "m", config_path=config_path)

        assert result.status == ConfigSetStatus.UNREADABLE
        assert result.error_code == ErrorCode.CONFIG_UNREADABLE
        assert result.errors == [f"Unable to read config.json at '{config_path}': denied."]
        assert result.remediations == [Remediation(message="Check file permissions and that the path is readable")]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "config.read"
        assert event["context"] == {"path": str(config_path)}
        assert event["exception_type"] == "PermissionError"

    def test_set_write_failure_reports_config_write_failed_and_leaves_file(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Report write_failed and CONFIG_WRITE_FAILED with one config.write record, leaving the file bytes unchanged."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        _write_default(config_path)
        before = config_path.read_bytes()
        _fail_writes(monkeypatch)

        result = set_config_value_result("agents.copilot.model", "m", config_path=config_path)

        assert result.status == ConfigSetStatus.WRITE_FAILED
        assert result.error_code == ErrorCode.CONFIG_WRITE_FAILED
        assert result.value == "m"
        assert result.errors == [f"Unable to write config.json at '{config_path}': disk full."]
        assert result.remediations == [Remediation(message="Check file permissions and free disk space")]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "config.write"
        assert event["context"] == {"path": str(config_path)}
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

    @pytest.mark.parametrize(
        ("write", "key", "status", "error_code", "error", "remediations", "existed"), UNSET_FAILURE_CASES
    )
    def test_unset_failure_reports_status_and_error_code(
        self,
        isolated_workspace: Path,
        write: Callable[[Path], object],
        key: str,
        status: ConfigUnsetStatus,
        error_code: ErrorCode,
        error: str,
        remediations: list[Remediation],
        existed: bool,
    ) -> None:
        """Report the row's status, error_code, errors, remediations, existed and previous value, record nothing, and leave the path unchanged."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        write(config_path)
        before = _snapshot(config_path)
        previous_value = json.loads(config_path.read_text(encoding="utf-8"))[key] if existed else None

        result = unset_config_value_result(key, config_path=config_path)

        assert result == ConfigUnsetResult(
            status=status,
            error_code=error_code,
            config_path=config_path,
            key=key,
            existed=existed,
            previous_value=previous_value,
            errors=[error.format(path=config_path)],
            remediations=remediations,
        )
        assert _snapshot(config_path) == before

    def test_unset_unreadable_config_copies_code_and_record(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Copy the read failure's CONFIG_UNREADABLE, errors, remediations and one config.read record."""
        config_path = isolated_workspace / ".dovo" / "config.json"
        _write_default(config_path)
        _deny_config_read(monkeypatch, config_path)

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.UNREADABLE
        assert result.error_code == ErrorCode.CONFIG_UNREADABLE
        assert result.errors == [f"Unable to read config.json at '{config_path}': denied."]
        assert result.remediations == [Remediation(message="Check file permissions and that the path is readable")]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        assert read_single_event(frozen_diagnostic_clock)["operation"] == "config.read"

    def test_unset_write_failure_returns_write_failed(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        config_path = isolated_workspace / ".dovo" / "config.json"
        payload = build_default_config("demo-workspace")
        payload["agents"] = {"copilot": {"model": "gpt-4.1"}}
        Filesystem.atomic_write_json(config_path, payload)
        before = config_path.read_bytes()
        _fail_writes(monkeypatch)

        result = unset_config_value_result("agents.copilot.model", config_path=config_path)

        assert result.status == ConfigUnsetStatus.WRITE_FAILED
        assert result.error_code == ErrorCode.CONFIG_WRITE_FAILED
        assert result.config_path == config_path
        assert result.key == "agents.copilot.model"
        assert result.existed is True
        assert result.previous_value == "gpt-4.1"
        assert result.errors == [f"Unable to write config.json at '{config_path}': disk full."]
        assert result.warnings == []
        assert result.remediations == [Remediation(message="Check file permissions and free disk space")]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "config.write"
        assert event["context"] == {"path": str(config_path)}
        assert config_path.read_bytes() == before
