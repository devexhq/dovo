"""Dot-path mutation helpers for `.dovo/config.json`."""

from __future__ import annotations

import copy
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from dovo.common.filesystem import Filesystem
from dovo.common.models import BaseResult
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.loader import (
    _map_dovo_config,
    clear_config_cache,
)
from dovo.core.config.parser import parse_config_value


class ConfigSetStatus(StrEnum):
    """Classified outcomes for setting a config value by dot-path."""

    OK = "ok"
    NOT_FOUND = "not_found"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    SCHEMA_INVALID = "schema_invalid"
    PATH_IS_DIRECTORY = "path_is_directory"
    UNREADABLE = "unreadable"
    TYPE_COLLISION = "type_collision"
    INVALID_PATH = "invalid_path"
    WRITE_FAILED = "write_failed"


class ConfigSetResult(BaseResult):
    """Non-raising result of a config dot-path set operation."""

    status: ConfigSetStatus
    config_path: Path
    key: str
    value: Any = None

    @property
    def ok(self) -> bool:
        """Return True when the value was written successfully."""
        return self.status == ConfigSetStatus.OK


class ConfigUnsetStatus(StrEnum):
    """Classified outcomes for removing a config value by dot-path."""

    OK = "ok"
    NOT_FOUND = "not_found"
    MALFORMED_JSON = "malformed_json"
    ROOT_NOT_OBJECT = "root_not_object"
    SCHEMA_INVALID = "schema_invalid"
    PATH_IS_DIRECTORY = "path_is_directory"
    UNREADABLE = "unreadable"
    TYPE_COLLISION = "type_collision"
    INVALID_PATH = "invalid_path"
    WRITE_FAILED = "write_failed"


class ConfigUnsetResult(BaseResult):
    """Non-raising result of a config dot-path unset operation."""

    status: ConfigUnsetStatus
    config_path: Path
    key: str
    existed: bool
    previous_value: Any = None

    @property
    def ok(self) -> bool:
        """Return True when the key was removed, or was already absent."""
        return self.status == ConfigUnsetStatus.OK


def set_nested_value(config_dict: dict[str, Any], dot_path: str, value: Any) -> None:
    """Set ``value`` at ``dot_path`` inside ``config_dict`` (in place).

    Intermediate missing segments are created as empty dicts. If an intermediate
    segment exists and is not a dict, raise ``ValueError``.

    Args:
        config_dict: Mutable configuration object root.
        dot_path: Dot-separated key path (e.g. ``agent.model``).
        value: Value to assign at the final segment.

    Raises:
        ValueError: Empty/invalid path or scalar-vs-dict type collision.
    """
    if not dot_path or not dot_path.strip():
        raise ValueError("Cannot set '': config key path must be a non-empty dot path.")

    keys = dot_path.split(".")
    if any(key == "" for key in keys):
        raise ValueError(f"Cannot set '{dot_path}': config key path contains an empty segment.")

    current: dict[str, Any] = config_dict
    for i, key in enumerate(keys[:-1]):
        if key not in current:
            current[key] = {}
        node = current[key]
        if not isinstance(node, dict):
            conflict_path = ".".join(keys[: i + 1])
            raise ValueError(f"Cannot set '{dot_path}'. '{conflict_path}' is already defined as a scalar value.")
        current = node

    current[keys[-1]] = value


def unset_nested_value(config_dict: dict[str, Any], dot_path: str) -> bool:
    """Remove the value at ``dot_path`` inside ``config_dict`` (in place).

    Traverses the same way ``set_nested_value`` does. A missing intermediate segment,
    or an absent final segment, is a no-op. Intermediate dicts left empty by a removal
    are not pruned, since every section model other than ``project`` fills its own
    defaults for absent keys.

    Args:
        config_dict: Mutable configuration object root.
        dot_path: Dot-separated key path (e.g. ``agent.model``).

    Returns:
        True if the key existed and was removed, False if it was a no-op.

    Raises:
        ValueError: Empty/invalid path or scalar-vs-dict type collision.
    """
    if not dot_path or not dot_path.strip():
        raise ValueError("Cannot unset '': config key path must be a non-empty dot path.")

    keys = dot_path.split(".")
    if any(key == "" for key in keys):
        raise ValueError(f"Cannot unset '{dot_path}': config key path contains an empty segment.")

    current: dict[str, Any] = config_dict
    for i, key in enumerate(keys[:-1]):
        if key not in current:
            return False
        node = current[key]
        if not isinstance(node, dict):
            conflict_path = ".".join(keys[: i + 1])
            raise ValueError(f"Cannot unset '{dot_path}'. '{conflict_path}' is already defined as a scalar value.")
        current = node

    final_key = keys[-1]
    if final_key not in current:
        return False

    del current[final_key]
    return True


def _peek_nested_value(config_dict: dict[str, Any], dot_path: str) -> Any:
    """Return the value at ``dot_path``, assuming the caller has already confirmed it exists."""
    current: Any = config_dict
    for key in dot_path.split("."):
        current = current[key]
    return current


def _read_config_object(path: Path, key: str) -> dict[str, Any] | ConfigSetResult:
    """Load ``config.json`` as an object, or return a classified failure."""
    if path.exists() and path.is_dir():
        return ConfigSetResult(
            status=ConfigSetStatus.PATH_IS_DIRECTORY,
            config_path=path,
            key=key,
            errors=[f"Config path is a directory, not a file: '{path}' (CONFIG_PATH_IS_DIRECTORY)."],
            fixes=["Remove the directory or point config_path at a file"],
        )

    if not path.exists():
        return ConfigSetResult(
            status=ConfigSetStatus.NOT_FOUND,
            config_path=path,
            key=key,
            errors=[f"Configuration file not found at '{path}' (CONFIG_NOT_FOUND)."],
            fixes=["Run `dovo init` to create `.dovo/config.json`"],
        )

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ConfigSetResult(
            status=ConfigSetStatus.UNREADABLE,
            config_path=path,
            key=key,
            errors=[f"Unable to read config.json at '{path}': {exc} (CONFIG_UNREADABLE)."],
            fixes=["Check file permissions and that the path is readable"],
        )

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        detail = f"line {exc.lineno} column {exc.colno} (char {exc.pos})"
        if exc.msg:
            detail = f"{exc.msg} at {detail}"
        return ConfigSetResult(
            status=ConfigSetStatus.MALFORMED_JSON,
            config_path=path,
            key=key,
            errors=[f"Malformed config.json at '{path}': {detail} (CONFIG_MALFORMED_JSON)."],
            fixes=["Repair JSON syntax, or restore from backup"],
        )

    if not isinstance(data, dict):
        return ConfigSetResult(
            status=ConfigSetStatus.ROOT_NOT_OBJECT,
            config_path=path,
            key=key,
            errors=[f"Malformed config.json at '{path}': root must be an object (CONFIG_ROOT_NOT_OBJECT)."],
            fixes=["Ensure config.json is a JSON object, not an array or scalar"],
        )

    return data


def _validate_mutated_config(
    updated: dict[str, Any],
    path: Path,
    key: str,
    value: Any = None,
) -> ConfigSetResult | None:
    """Return a schema-invalid result, or ``None`` when mapping succeeds."""
    validation = CONFIG_VALIDATOR.validate(updated)
    if not validation.ok:
        return ConfigSetResult(
            status=ConfigSetStatus.SCHEMA_INVALID,
            config_path=path,
            key=key,
            value=value,
            errors=[
                "\n".join(
                    [
                        "Config schema validation failed (CONFIG_SCHEMA_INVALID):",
                        *(f"- {msg}" for msg in validation.errors),
                    ]
                )
            ],
            fixes=[
                "Run `dovo config validate` for details",
                "Or `dovo init --repair` to insert missing keys without overwriting values",
            ],
        )

    try:
        _map_dovo_config(updated)
    except Exception as exc:
        return ConfigSetResult(
            status=ConfigSetStatus.SCHEMA_INVALID,
            config_path=path,
            key=key,
            value=value,
            errors=[
                "\n".join(
                    [
                        "Config schema validation failed (CONFIG_SCHEMA_INVALID):",
                        *(f"- {msg}" for msg in [str(exc)]),
                    ]
                )
            ],
            fixes=[
                "Run `dovo config validate` for details",
                "Or `dovo init --repair` to insert missing keys without overwriting values",
            ],
        )

    return None


def set_config_value_result(
    key: str,
    value: Any,
    *,
    config_path: Path,
) -> ConfigSetResult:
    """Load config JSON, set a dot-path value, and persist on success.

    Does not print or call ``sys.exit``. Enforces schema key allow-lists and V1 schema validation.

    Args:
        key: Dot-path key to set.
        value: Native Python value (or string) to assign.
        config_path: Resolved configuration file path.

    Returns:
        Classified ``ConfigSetResult`` with absolute ``config_path``.
    """
    loaded = _read_config_object(config_path, key)
    if isinstance(loaded, ConfigSetResult):
        return loaded

    parsed_value = parse_config_value(value) if isinstance(value, str) else value
    updated = copy.deepcopy(loaded)
    try:
        set_nested_value(updated, key, parsed_value)
    except ValueError as exc:
        message = str(exc)
        status = (
            ConfigSetStatus.TYPE_COLLISION
            if "already defined as a scalar value" in message
            else ConfigSetStatus.INVALID_PATH
        )
        return ConfigSetResult(
            status=status,
            config_path=config_path,
            key=key,
            value=parsed_value,
            errors=[message],
        )

    schema_error = _validate_mutated_config(updated, config_path, key, value=parsed_value)
    if schema_error is not None:
        return schema_error

    try:
        Filesystem.atomic_write_json(config_path, updated)
    except OSError as exc:
        return ConfigSetResult(
            status=ConfigSetStatus.WRITE_FAILED,
            config_path=config_path,
            key=key,
            value=parsed_value,
            errors=[f"Unable to write config.json at '{config_path}': {exc} (CONFIG_WRITE_FAILED)."],
            fixes=["Check file permissions and free disk space"],
        )

    clear_config_cache(config_path)
    return ConfigSetResult(
        status=ConfigSetStatus.OK,
        config_path=config_path,
        key=key,
        value=parsed_value,
        errors=[],
    )


def unset_config_value_result(
    key: str,
    *,
    config_path: Path,
) -> ConfigUnsetResult:
    """Load config JSON, remove a dot-path value, and persist on success.

    Does not print or call ``sys.exit``. Reuses ``_read_config_object`` and
    ``_validate_mutated_config`` (which return ``ConfigSetResult``-shaped failures)
    and translates their classification into ``ConfigUnsetResult``.

    Args:
        key: Dot-path key to remove.
        config_path: Resolved configuration file path.

    Returns:
        Classified ``ConfigUnsetResult`` with absolute ``config_path``.
    """
    loaded = _read_config_object(config_path, key)
    if isinstance(loaded, ConfigSetResult):
        return ConfigUnsetResult(
            status=ConfigUnsetStatus(loaded.status.value),
            config_path=config_path,
            key=key,
            existed=False,
            errors=loaded.errors,
            fixes=loaded.fixes,
        )

    updated = copy.deepcopy(loaded)
    try:
        existed = unset_nested_value(updated, key)
    except ValueError as exc:
        message = str(exc)
        status = (
            ConfigUnsetStatus.TYPE_COLLISION
            if "already defined as a scalar value" in message
            else ConfigUnsetStatus.INVALID_PATH
        )
        return ConfigUnsetResult(
            status=status,
            config_path=config_path,
            key=key,
            existed=False,
            errors=[message],
        )

    if not existed:
        return ConfigUnsetResult(
            status=ConfigUnsetStatus.OK,
            config_path=config_path,
            key=key,
            existed=False,
        )

    previous_value = _peek_nested_value(loaded, key)

    schema_error = _validate_mutated_config(updated, config_path, key)
    if schema_error is not None:
        return ConfigUnsetResult(
            status=ConfigUnsetStatus(schema_error.status.value),
            config_path=config_path,
            key=key,
            existed=True,
            previous_value=previous_value,
            errors=schema_error.errors,
            fixes=schema_error.fixes,
        )

    try:
        Filesystem.atomic_write_json(config_path, updated)
    except OSError as exc:
        return ConfigUnsetResult(
            status=ConfigUnsetStatus.WRITE_FAILED,
            config_path=config_path,
            key=key,
            existed=True,
            previous_value=previous_value,
            errors=[f"Unable to write config.json at '{config_path}': {exc} (CONFIG_WRITE_FAILED)."],
            fixes=["Check file permissions and free disk space"],
        )

    clear_config_cache(config_path)
    return ConfigUnsetResult(
        status=ConfigUnsetStatus.OK,
        config_path=config_path,
        key=key,
        existed=True,
        previous_value=previous_value,
        errors=[],
    )
