"""Dot-path mutation of `.dovo/config.json`: the set and unset results, and helpers that raise ConfigError."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import BaseResult, Remediation
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.exceptions import ConfigError
from dovo.core.config.loader import _map_dovo_config, _schema_error, clear_config_cache


class ConfigSetResult(BaseResult):
    """Result of `Config.set`: the key and its coerced value, or the failure's error code, errors and remediations."""

    config_path: Path
    key: str
    value: Any = None


class ConfigUnsetResult(BaseResult):
    """Result of `Config.unset`: whether the key existed and its previous value, plus any failure."""

    config_path: Path
    key: str
    existed: bool
    previous_value: Any = None


@dataclass(frozen=True, slots=True)
class ConfigRemoval:
    """An unset that found its key: the config object without it, and the value the key held."""

    updated: dict[str, object]
    previous_value: object


def _split_key_path(verb: str, dot_path: str) -> list[str]:
    """Return the segments of dot_path.

    Raises:
        ConfigError: CONFIG_KEY_PATH_INVALID for an empty dot_path or one with an empty segment.
    """
    if not dot_path or not dot_path.strip():
        raise ConfigError(
            ErrorCode.CONFIG_KEY_PATH_INVALID, f"Cannot {verb} '': config key path must be a non-empty dot path."
        )
    keys = dot_path.split(".")
    if any(key == "" for key in keys):
        raise ConfigError(
            ErrorCode.CONFIG_KEY_PATH_INVALID,
            f"Cannot {verb} '{dot_path}': config key path contains an empty segment.",
        )
    return keys


def _type_conflict(verb: str, dot_path: str, keys: list[str], depth: int) -> ConfigError:
    """Return the CONFIG_TYPE_CONFLICT error for dot_path crossing the scalar at keys[: depth + 1]."""
    conflict_path = ".".join(keys[: depth + 1])
    return ConfigError(
        ErrorCode.CONFIG_TYPE_CONFLICT,
        f"Cannot {verb} '{dot_path}'. '{conflict_path}' is already defined as a scalar value.",
    )


def set_nested_value(config_dict: dict[str, object], dot_path: str, value: object) -> None:
    """Set ``value`` at ``dot_path`` inside ``config_dict`` (in place).

    Intermediate missing segments are created as empty dicts.

    Raises:
        ConfigError: CONFIG_KEY_PATH_INVALID for a malformed path, CONFIG_TYPE_CONFLICT when a segment is a scalar.
    """
    keys = _split_key_path("set", dot_path)

    current: dict[str, object] = config_dict
    for i, key in enumerate(keys[:-1]):
        node = current.setdefault(key, {})
        if not isinstance(node, dict):
            raise _type_conflict("set", dot_path, keys, i)
        current = node

    current[keys[-1]] = value


def unset_nested_value(config_dict: dict[str, object], dot_path: str) -> bool:
    """Remove the value at ``dot_path`` inside ``config_dict`` (in place).

    Traverses the same way ``set_nested_value`` does. A missing intermediate segment,
    or an absent final segment, is a no-op. Intermediate dicts left empty by a removal
    are not pruned, since every section model other than ``project`` fills its own
    defaults for absent keys.

    Returns:
        True if the key existed and was removed, False if it was a no-op.

    Raises:
        ConfigError: CONFIG_KEY_PATH_INVALID for a malformed path, CONFIG_TYPE_CONFLICT when a segment is a scalar.
    """
    keys = _split_key_path("unset", dot_path)

    current: dict[str, object] = config_dict
    for i, key in enumerate(keys[:-1]):
        if key not in current:
            return False
        node = current[key]
        if not isinstance(node, dict):
            raise _type_conflict("unset", dot_path, keys, i)
        current = node

    final_key = keys[-1]
    if final_key not in current:
        return False

    del current[final_key]
    return True


def _peek_nested_value(config_dict: dict[str, object], dot_path: str) -> object:
    """Return the value at ``dot_path``, which the caller has already confirmed exists."""
    current: object = config_dict
    for key in dot_path.split("."):
        if not isinstance(current, dict):
            raise RuntimeError(f"'{dot_path}' does not name a value in the config object")
        current = current[key]
    return current


def with_value_set(loaded: dict[str, object], key: str, value: JsonValue) -> dict[str, object]:
    """Return a copy of loaded with value set at the dot-path key.

    Raises:
        ConfigError: CONFIG_KEY_PATH_INVALID or CONFIG_TYPE_CONFLICT, from ``set_nested_value``.
    """
    updated = copy.deepcopy(loaded)
    set_nested_value(updated, key, value)
    return updated


def without_value(loaded: dict[str, object], key: str) -> ConfigRemoval | None:
    """Return a copy of loaded without the dot-path key and the value it held, or None when the key is absent.

    Raises:
        ConfigError: CONFIG_KEY_PATH_INVALID or CONFIG_TYPE_CONFLICT, from ``unset_nested_value``.
    """
    updated = copy.deepcopy(loaded)
    if not unset_nested_value(updated, key):
        return None
    return ConfigRemoval(updated=updated, previous_value=_peek_nested_value(loaded, key))


def write_mutated_config(path: Path, updated: dict[str, object]) -> None:
    """Validate updated against the schema and config model, then write it to path atomically.

    Raises:
        ConfigError: CONFIG_SCHEMA_INVALID when updated fails validation, CONFIG_WRITE_FAILED from the write's OSError.
    """
    schema_errors = CONFIG_VALIDATOR.validate(updated)
    if schema_errors:
        raise _schema_error(list(schema_errors), updated)
    try:
        _map_dovo_config(updated)
    except ValueError as exc:
        raise _schema_error([str(exc)], updated) from exc

    try:
        Filesystem.atomic_write_json(path, updated)
    except OSError as exc:
        raise ConfigError(
            ErrorCode.CONFIG_WRITE_FAILED,
            f"Unable to write config.json at '{path}': {exc}.",
            [Remediation(message="Check file permissions and free disk space")],
        ) from exc
    clear_config_cache(path)
