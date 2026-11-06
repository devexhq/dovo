"""Load and validate `.dovo/config.json`, raising ConfigError when it cannot be loaded."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import BaseResult, Remediation
from dovo.common.schema_validation import CONFIG_VALIDATOR, format_validation_error
from dovo.core.config.exceptions import ConfigError, ConfigRawObject
from dovo.core.config.models import DovoConfig


class ConfigLoadResult(BaseResult):
    """Result of `Config.load`: the loaded config, or the failure's error code, errors and remediations."""

    config_path: Path
    raw: dict[str, Any] | None = None
    config: DovoConfig | None = None


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """A config.json that loaded and validated: its path, raw JSON object and typed model."""

    config_path: Path
    raw: dict[str, object]
    config: DovoConfig


@dataclass
class _CachedConfig:
    mtime_ns: int
    size: int
    loaded: LoadedConfig


_CONFIG_CACHE: dict[Path, _CachedConfig] = {}


def clear_config_cache(config_path: Path | None = None) -> None:
    """Explicitly clear in-memory cached configuration."""
    if config_path is not None:
        _CONFIG_CACHE.pop(config_path, None)
    else:
        _CONFIG_CACHE.clear()


def resolve_config_path(paths: WorkspacePaths, *, config_path: Path | None = None) -> Path:
    """Return absolute path to config.json.

    Args:
        paths: Resolved command-invocation workspace paths.
        config_path: Explicit config path; wins when provided.

    Returns:
        Absolute path to the config file.
    """
    return config_path.expanduser().resolve() if config_path is not None else paths.config_file


_SCHEMA_REMEDIATIONS = (
    Remediation(message="Run 'dovo config validate' for details", command="dovo config validate"),
    Remediation(
        message="Run 'dovo init --repair' to insert missing keys without overwriting values",
        command="dovo init --repair",
    ),
)


def _schema_error(messages: list[str], raw: dict[str, object]) -> ConfigError:
    """Return the CONFIG_SCHEMA_INVALID error listing messages under the schema-failure heading, carrying raw."""
    return ConfigError(
        ErrorCode.CONFIG_SCHEMA_INVALID,
        "\n".join(["Config schema validation failed:", *(f"- {msg}" for msg in messages)]),
        _SCHEMA_REMEDIATIONS,
        data=ConfigRawObject(raw),
    )


def _read_config_text(target_path: Path) -> str:
    """Return the text of config.json at target_path.

    Raises:
        ConfigError: CONFIG_UNREADABLE, from the OSError that prevented the read.
    """
    try:
        return target_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            ErrorCode.CONFIG_UNREADABLE,
            f"Unable to read config.json at '{target_path}': {exc}.",
            [Remediation(message="Check file permissions and that the path is readable")],
        ) from exc


def _parse_config_object(text: str, target_path: Path) -> dict[str, object]:
    """Return the JSON object text holds.

    Raises:
        ConfigError: CONFIG_JSON_INVALID for malformed JSON, CONFIG_ROOT_INVALID for a non-object root.
    """
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        detail = f"line {exc.lineno} column {exc.colno} (char {exc.pos})"
        if exc.msg:
            detail = f"{exc.msg} at {detail}"
        raise ConfigError(
            ErrorCode.CONFIG_JSON_INVALID,
            f"Malformed config.json at '{target_path}': {detail}.",
            [Remediation(message="Repair JSON syntax, or restore from backup")],
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            ErrorCode.CONFIG_ROOT_INVALID,
            f"Malformed config.json at '{target_path}': root must be an object.",
            [Remediation(message="Ensure 'config.json' is a JSON object, not an array or scalar")],
        )
    return data


def _read_and_validate_disk_config(target_path: Path) -> LoadedConfig:
    """Read, parse, and validate config.json from disk.

    Raises:
        ConfigError: When the file cannot be read, parsed or validated.
    """
    data = _parse_config_object(_read_config_text(target_path), target_path)

    schema_errors = CONFIG_VALIDATOR.validate(data)
    if schema_errors:
        raise _schema_error(list(schema_errors), data)

    try:
        config = _map_dovo_config(data)
    except ValueError as exc:
        raise _schema_error([str(exc)], data) from exc

    return LoadedConfig(config_path=target_path, raw=data, config=config)


def read_config_object(target_path: Path) -> dict[str, object]:
    """Return the JSON object in config.json at target_path, without schema validation.

    Raises:
        ConfigError: When the path is a directory or missing, or the file is unreadable, malformed or not an object.
    """
    _check_path_existence(target_path)
    return _parse_config_object(_read_config_text(target_path), target_path)


def _check_path_existence(target_path: Path) -> None:
    """Require target_path to exist as a file.

    Raises:
        ConfigError: CONFIG_PATH_INVALID for a directory, CONFIG_NOT_FOUND for a missing path.
    """
    if target_path.exists() and target_path.is_dir():
        _CONFIG_CACHE.pop(target_path, None)
        raise ConfigError(
            ErrorCode.CONFIG_PATH_INVALID,
            f"Config path is a directory, not a file: '{target_path}'.",
            [Remediation(message="Remove the directory or point config_path at a file")],
        )

    if not target_path.exists():
        _CONFIG_CACHE.pop(target_path, None)
        raise ConfigError(
            ErrorCode.CONFIG_NOT_FOUND,
            f"Configuration file not found at '{target_path}'.",
            [Remediation(message="Run 'dovo init' to create '.dovo/config.json'", command="dovo init")],
        )


def _get_cached_config(
    target_path: Path,
    stat: os.stat_result | None,
    bypass_cache: bool,
) -> LoadedConfig | None:
    """Return the cached LoadedConfig if stat matches the in-memory entry."""
    if bypass_cache or stat is None:
        return None
    cached = _CONFIG_CACHE.get(target_path)
    if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
        return cached.loaded
    return None


def load_config(
    paths: WorkspacePaths,
    *,
    config_path: Path | None = None,
    bypass_cache: bool = False,
) -> LoadedConfig:
    """Load and validate config.json.

    Does not print, exit, create, or mutate config files. Uses an in-memory
    cache validated against file modification time.

    Args:
        paths: Resolved command-invocation workspace paths.
        config_path: Explicit path override.
        bypass_cache: When True, bypasses the in-memory cache and reads disk directly.

    Returns:
        The loaded config with its absolute ``config_path``.

    Raises:
        ConfigError: When the file is missing, unreadable, malformed or fails schema validation.
    """
    target_path = resolve_config_path(paths, config_path=config_path)
    return load_config_at(target_path, bypass_cache=bypass_cache)


def load_config_at(target_path: Path, *, bypass_cache: bool = False) -> LoadedConfig:
    """Load and validate config.json at an already-resolved path, without any ambient fallback.

    Args:
        target_path: Absolute path to the config.json file to load.
        bypass_cache: When True, bypasses the in-memory cache and reads disk directly.

    Returns:
        The loaded config with its absolute ``config_path``.

    Raises:
        ConfigError: When the file is missing, unreadable, malformed or fails schema validation.
    """
    _check_path_existence(target_path)

    try:
        stat = target_path.stat()
    except OSError:
        stat = None

    cached = _get_cached_config(target_path, stat, bypass_cache)
    if cached is not None:
        return cached

    loaded = _read_and_validate_disk_config(target_path)
    if stat is not None:
        _CONFIG_CACHE[target_path] = _CachedConfig(mtime_ns=stat.st_mtime_ns, size=stat.st_size, loaded=loaded)
    return loaded


def _map_dovo_config(raw: dict[str, Any]) -> DovoConfig:
    """Map a schema-valid raw dict into ``DovoConfig``."""
    project_raw = raw.get("project", {})
    project_name = project_raw.get("name", "unnamed_project")
    normalized = {
        **raw,
        "project": {
            **project_raw,
            "name": str(project_name),
        },
    }
    try:
        return DovoConfig.model_validate(normalized)
    except ValidationError as exc:
        raise ValueError(format_validation_error(exc)) from exc
