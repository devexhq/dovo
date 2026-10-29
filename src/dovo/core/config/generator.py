"""Generate and persist default `.dovo/config.json` (V1)."""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dovo.common.constants import DEFAULT_MAXIMUM_WORKTREES_ALLOWED
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import Remediation
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.exceptions import ConfigError
from dovo.core.config.loader import clear_config_cache
from dovo.core.config.models import ConfigGenerationSummary

CANONICAL_V1_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "project": {
        "name": None,
        "initialized_at": None,
    },
    "ignore_global_root_error": False,
    "worktree": {
        "base_ref": "HEAD",
        "max_active_worktrees": DEFAULT_MAXIMUM_WORKTREES_ALLOWED,
        "default_timeout_seconds": 900,
    },
    "default_agent": "copilot",
    "agents": {},
    "history": {
        "save_attempt_logs": True,
        "save_agent_payloads": True,
        "save_final_diff": True,
        "max_sessions": 1000,
    },
    "doctor": {
        "check_git": True,
        "check_paths_writable": True,
        "check_config_schema": True,
        "check_stale_worktrees": True,
        "check_required_binaries": True,
    },
    "prune": {
        "remove_stale_worktrees": True,
        "remove_orphaned_worktrees": True,
        "remove_expired_artifacts": False,
        "artifact_ttl_days": 30,
    },
    "telemetry": {
        "enabled": False,
    },
    "concurrency": {
        "lock_timeout_seconds": 30.0,
    },
    "environment": {
        "sensitive_variables": [],
    },
}

_INVALID_JSON_REMEDIATIONS = (
    Remediation(message="Repair invalid JSON manually"),
    Remediation(message="Run 'dovo init --overwrite'", command="dovo init --overwrite"),
)


def build_default_config(project_name: str) -> dict[str, Any]:
    """Return canonical V1 defaults with runtime project fields set."""
    config = copy.deepcopy(CANONICAL_V1_DEFAULTS)
    config["project"]["name"] = project_name
    config["project"]["initialized_at"] = datetime.now(UTC).isoformat()
    return config


def merge_missing_keys(
    existing: dict[str, Any],
    defaults: dict[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    """Insert missing keys from ``defaults`` into ``existing`` (non-destructive)."""
    inserted: list[str] = []
    for key, default_val in defaults.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in existing:
            existing[key] = copy.deepcopy(default_val)
            inserted.append(path)
        elif isinstance(default_val, dict) and isinstance(existing.get(key), dict):
            inserted.extend(merge_missing_keys(existing[key], default_val, prefix=path))
    return inserted


def _preflight_config_path(config_path: Path) -> None:
    """Raise ConfigError when the config path cannot be used."""
    if config_path.exists() and config_path.is_dir():
        raise ConfigError(ErrorCode.CONFIG_PATH_INVALID, f"Config path '{config_path.as_posix()}' is a directory.")
    parent = config_path.parent
    if not parent.exists():
        raise ConfigError(ErrorCode.CONFIG_PARENT_MISSING, f"Config directory '{parent.as_posix()}' does not exist.")
    if not os.access(parent, os.W_OK):
        raise ConfigError(
            ErrorCode.CONFIG_PATH_UNWRITABLE,
            f"Config path '{config_path.as_posix()}' is not writable.",
            [Remediation(message=f"Ensure write permissions for '{parent.as_posix()}' directory")],
        )


def _validate_and_write(config_path: Path, payload: dict[str, object], *, label: str) -> None:
    """Validate payload against the config schema, then write it atomically; raise ConfigError on either failure."""
    schema_errors = CONFIG_VALIDATOR.validate(payload)
    if schema_errors:
        raise ConfigError(
            ErrorCode.CONFIG_SCHEMA_INVALID,
            "\n".join(f"{label} config failed validation: {e}" for e in schema_errors),
        )
    try:
        Filesystem.atomic_write_json(config_path, payload)
    except OSError as exc:
        raise ConfigError(
            ErrorCode.CONFIG_WRITE_FAILED, f"Unable to write config at '{config_path.as_posix()}': {exc}"
        ) from exc
    clear_config_cache(config_path)


def _repair_existing_config(config_path: Path, project_name: str) -> list[str]:
    """Insert missing default keys into the existing config, preserving custom values; return the inserted keys."""
    try:
        with open(config_path, encoding="utf-8") as f:
            existing: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            ErrorCode.CONFIG_JSON_INVALID,
            f"Invalid config JSON at '{config_path.as_posix()}': {exc}",
            _INVALID_JSON_REMEDIATIONS,
        ) from exc
    if not isinstance(existing, dict):
        raise ConfigError(
            ErrorCode.CONFIG_ROOT_INVALID,
            f"Invalid config JSON at '{config_path.as_posix()}': root must be a JSON object",
        )

    defaults = build_default_config(project_name)
    if existing.get("project", {}).get("initialized_at"):
        defaults["project"]["initialized_at"] = existing["project"]["initialized_at"]
    if existing.get("project", {}).get("name"):
        defaults["project"]["name"] = existing["project"]["name"]

    inserted = merge_missing_keys(existing, defaults)
    _validate_and_write(config_path, existing, label="Repaired")
    return inserted


def generate_default_config(
    config_path: Path,
    project_name: str,
    *,
    overwrite: bool = False,
    repair: bool = False,
) -> ConfigGenerationSummary:
    """Create, skip, repair, or overwrite ``config_path`` per init policy.

    Raises:
        ConfigError: If the path cannot be used, the existing config cannot be repaired,
            the payload fails schema validation, or the write fails. Nothing is written.
    """
    config_path = config_path.resolve()
    warnings = ("repair ignored when overwrite=True",) if overwrite and repair else ()
    _preflight_config_path(config_path)
    existed_before = config_path.is_file()
    created = skipped_existing = repaired = overwritten = False
    inserted_keys: list[str] = []

    if existed_before and not overwrite and not repair:
        skipped_existing = True
    elif overwrite or not existed_before:
        _validate_and_write(config_path, build_default_config(project_name), label="Generated")
        overwritten = existed_before
        created = not existed_before
    else:
        inserted_keys = _repair_existing_config(config_path, project_name)
        repaired = True

    return ConfigGenerationSummary(
        config_path=config_path,
        created=created,
        skipped_existing=skipped_existing,
        repaired=repaired,
        overwritten=overwritten,
        inserted_keys=tuple(inserted_keys),
        warnings=warnings,
    )
