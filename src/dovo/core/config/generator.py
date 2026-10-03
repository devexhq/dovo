"""Generate and persist default `.dovo/config.json` (V1)."""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from dovo.common.constants import DEFAULT_MAXIMUM_SANDBOXES_ALLOWED
from dovo.common.filesystem import Filesystem
from dovo.common.models import BaseResult
from dovo.common.schema_validation import CONFIG_VALIDATOR
from dovo.core.config.loader import clear_config_cache

CANONICAL_V1_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "project": {
        "name": None,
        "initialized_at": None,
    },
    "ignore_global_root_error": False,
    "sandbox": {
        "base_ref": "HEAD",
        "max_active_sandboxes": DEFAULT_MAXIMUM_SANDBOXES_ALLOWED,
        "default_timeout_seconds": 900,
    },
    "agent": {
        "provider": "local",
        "model": None,
        "endpoint": None,
        "temperature": 0.2,
        "max_tokens": 4096,
    },
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
        "remove_orphaned_sandboxes": True,
        "remove_expired_artifacts": False,
        "artifact_ttl_days": 30,
    },
    "telemetry": {
        "enabled": False,
    },
    "concurrency": {
        "lock_timeout_seconds": 30.0,
    },
}


# @TODO: Move this to models.py
class ConfigGenerationResult(BaseResult):
    """Outcome of attempting to create, skip, repair, or overwrite config."""

    created: bool = False
    skipped_existing: bool = False
    repaired: bool = False
    overwritten: bool = False
    inserted_keys: list[str] = Field(default_factory=list)
    config_path: Path | None = None

    @property
    def ok(self) -> bool:
        """True when generation completed without blocking errors."""
        return not self.errors


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


def _preflight_config_path(config_path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, fixes) if the config path cannot be used."""
    if config_path.exists() and config_path.is_dir():
        return [f"CONFIG_PATH_IS_DIRECTORY at {config_path.as_posix()}"], []
    parent = config_path.parent
    if not parent.exists():
        return [f"CONFIG_PARENT_MISSING at {parent.as_posix()}"], []
    if not os.access(parent, os.W_OK):
        return [f"CONFIG_PATH_NOT_WRITABLE at {config_path.as_posix()}"], [
            f"Ensure write permissions for {parent.as_posix()} directory"
        ]
    return [], []


def _write_fresh_config(
    config_path: Path,
    project_name: str,
    result: ConfigGenerationResult,
    *,
    existed_before: bool,
    overwrite: bool,
) -> ConfigGenerationResult:
    """Validate and atomically write fresh default configuration payload."""
    payload = build_default_config(project_name)
    validation = CONFIG_VALIDATOR.validate(payload)
    if not validation.ok:
        result.errors.extend([f"CONFIG_VALIDATION_FAILED: {e}" for e in validation.errors])
        return result
    try:
        Filesystem.atomic_write_json(config_path, payload)
    except OSError as exc:
        result.errors.append(f"CONFIG_WRITE_FAILED at {config_path.as_posix()}: {exc}")
        return result
    clear_config_cache(config_path)
    if existed_before and overwrite:
        result.overwritten = True
    else:
        result.created = True
    return result


def _repair_existing_config(
    config_path: Path,
    project_name: str,
    result: ConfigGenerationResult,
) -> ConfigGenerationResult:
    """Repair missing keys in existing configuration while preserving custom values."""
    try:
        with open(config_path, encoding="utf-8") as f:
            existing: dict[str, Any] = json.load(f)
        if not isinstance(existing, dict):
            result.errors.append("CONFIG_INVALID_JSON: root must be a JSON object")
            return result
    except json.JSONDecodeError as exc:
        result.errors.append(f"CONFIG_INVALID_JSON at {config_path.as_posix()}: {exc}")
        result.fixes.append("Repair invalid JSON manually, or run dovo init --overwrite")
        return result

    defaults = build_default_config(project_name)
    if existing.get("project", {}).get("initialized_at"):
        defaults["project"]["initialized_at"] = existing["project"]["initialized_at"]
    if existing.get("project", {}).get("name"):
        defaults["project"]["name"] = existing["project"]["name"]

    inserted = merge_missing_keys(existing, defaults)
    validation = CONFIG_VALIDATOR.validate(existing)
    if not validation.ok:
        result.errors.extend([f"CONFIG_VALIDATION_FAILED: {e}" for e in validation.errors])
        return result
    try:
        Filesystem.atomic_write_json(config_path, existing)
    except OSError as exc:
        result.errors.append(f"CONFIG_WRITE_FAILED at {config_path.as_posix()}: {exc}")
        return result

    clear_config_cache(config_path)
    result.repaired = True
    result.inserted_keys = inserted
    return result


def generate_default_config(
    config_path: Path,
    project_name: str,
    *,
    overwrite: bool = False,
    repair: bool = False,
) -> ConfigGenerationResult:
    """Create, skip, repair, or overwrite ``config_path`` per init policy."""
    config_path = config_path.resolve()
    result = ConfigGenerationResult(config_path=config_path)

    if overwrite and repair:
        result.warnings.append("repair ignored when overwrite=True")

    preflight_errors, preflight_fixes = _preflight_config_path(config_path)
    if preflight_errors:
        result.errors.extend(preflight_errors)
        result.fixes.extend(preflight_fixes)
        return result

    existed_before = config_path.is_file()

    if existed_before and not overwrite and not repair:
        result.skipped_existing = True
        return result

    if overwrite or not existed_before:
        return _write_fresh_config(
            config_path,
            project_name,
            result,
            existed_before=existed_before,
            overwrite=overwrite,
        )

    return _repair_existing_config(config_path, project_name, result)
