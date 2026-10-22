"""Resolve and merge the Packaged, Global, User, and Repo configuration tiers."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from dovo.common.filesystem import WorkspacePaths
from dovo.common.schema_validation import format_validation_error
from dovo.core.config.exceptions import ConfigTierValidationError
from dovo.core.config.generator import CANONICAL_V1_DEFAULTS
from dovo.core.config.models import (
    ConfigTier,
    ConfigTierEntry,
    DovoConfig,
    TieredConfigLoadResult,
    TieredConfigLoadStatus,
)


def _packaged_defaults() -> dict[str, Any]:
    """Return the embedded baseline DovoConfig payload for the packaged tier."""
    defaults = copy.deepcopy(CANONICAL_V1_DEFAULTS)
    defaults["project"] = {"name": "unnamed_project", "initialized_at": None}
    return defaults


def _read_tier_file(tier: ConfigTier, path: Path) -> dict[str, Any] | None:
    """Read and parse one tier's config.json, returning None when the file is absent."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigTierValidationError(
            tier, path, f"unable to read file: {exc}. Check file permissions and that the path is readable."
        ) from exc

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        detail = f"line {exc.lineno} column {exc.colno} (char {exc.pos})"
        if exc.msg:
            detail = f"{exc.msg} at {detail}"
        raise ConfigTierValidationError(tier, path, detail) from exc

    if not isinstance(data, dict):
        raise ConfigTierValidationError(tier, path, "root must be a JSON object")

    return data


_ATOMIC_KEY_SUFFIX: Final[str] = "tools"


def _is_atomic_key_path(path: tuple[str, ...]) -> bool:
    """Return True for ``agents.<provider>.tools``, which a higher tier replaces whole."""
    return len(path) == 3 and path[0] == "agents" and path[2] == _ATOMIC_KEY_SUFFIX


def _deep_merge(base: dict[str, Any], override: dict[str, Any], path: tuple[str, ...] = ()) -> dict[str, Any]:
    """Recursively merge override onto a copy of base, replacing values at atomic key paths whole."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if not _is_atomic_key_path((*path, key)) and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value, (*path, key))
        else:
            result[key] = copy.deepcopy(value)
    return result


def _union_sensitive_variables(entries: Sequence[ConfigTierEntry]) -> list[str]:
    """Return environment.sensitive_variables from every tier in precedence order, first occurrence kept."""
    names: dict[str, None] = {}
    for entry in entries:
        for name in entry.data.get("environment", {}).get("sensitive_variables", []):
            names.setdefault(name)
    return list(names)


def _validate_merged_tier(tier: ConfigTier, path: Path, merged: dict[str, Any]) -> None:
    """Validate the running merged dict against DovoConfig, attributing failure to tier."""
    try:
        DovoConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigTierValidationError(tier, path, format_validation_error(exc)) from exc


def resolve_config_tiers(paths: WorkspacePaths) -> list[ConfigTierEntry]:
    """Resolve the Packaged, Global, User, and Repo config tiers present on disk, in precedence order."""
    entries = [ConfigTierEntry(tier=ConfigTier.PACKAGED, path=None, data=_packaged_defaults())]

    tier_paths = (
        (ConfigTier.GLOBAL, paths.global_paths.global_dir / "config.json"),
        (ConfigTier.USER, paths.global_paths.user_dir / "config.json"),
        (ConfigTier.REPO, paths.config_file),
    )
    for tier, path in tier_paths:
        data = _read_tier_file(tier, path)
        if data is not None:
            entries.append(ConfigTierEntry(tier=tier, path=path, data=data))

    return entries


def _classify_tier_error(details: str) -> TieredConfigLoadStatus:
    """Map a ConfigTierValidationError's details text to a load-result status."""
    if "Check file permissions and that the path is readable" in details:
        return TieredConfigLoadStatus.UNREADABLE
    if details == "root must be a JSON object":
        return TieredConfigLoadStatus.ROOT_NOT_OBJECT
    if "line" in details and "column" in details:
        return TieredConfigLoadStatus.MALFORMED_JSON
    return TieredConfigLoadStatus.VALIDATION_FAILED


def load_tiered_config(paths: WorkspacePaths) -> TieredConfigLoadResult:
    """Merge Packaged, Global, User, and Repo config tiers into one validated DovoConfig, without raising."""
    try:
        entries = resolve_config_tiers(paths)

        merged = _deep_merge({}, entries[0].data)
        for entry in entries[1:]:
            merged = _deep_merge(merged, entry.data)
            if entry.path is None:
                continue
            _validate_merged_tier(entry.tier, entry.path, merged)

        merged["environment"]["sensitive_variables"] = _union_sensitive_variables(entries)
        config = DovoConfig.model_validate(merged)
    except ConfigTierValidationError as exc:
        return TieredConfigLoadResult(
            status=_classify_tier_error(exc.details),
            tier=exc.tier,
            path=exc.path,
            errors=[str(exc)],
        )

    return TieredConfigLoadResult(status=TieredConfigLoadStatus.OK, config=config)
