"""Resolve and merge the Packaged, Global, User, and Repo configuration tiers."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dovo.common.filesystem import WorkspacePaths
from dovo.common.schema_validation import format_validation_error
from dovo.core.config.exceptions import ConfigTierValidationError
from dovo.core.config.generator import CANONICAL_V1_DEFAULTS
from dovo.core.config.models import (
    ConfigLayer,
    ConfigTier,
    DovoConfig,
    HierarchicalConfigLoadResult,
    HierarchicalConfigLoadStatus,
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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override onto a copy of base without mutating either argument."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _union_sensitive_variables(layers: Sequence[ConfigLayer]) -> list[str]:
    """Return environment.sensitive_variables from every layer in precedence order, first occurrence kept."""
    names: dict[str, None] = {}
    for layer in layers:
        for name in layer.data.get("environment", {}).get("sensitive_variables", []):
            names.setdefault(name)
    return list(names)


def _validate_merged_layer(tier: ConfigTier, path: Path, merged: dict[str, Any]) -> None:
    """Validate the running merged dict against DovoConfig, attributing failure to tier."""
    try:
        DovoConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigTierValidationError(tier, path, format_validation_error(exc)) from exc


def resolve_config_layers(paths: WorkspacePaths) -> list[ConfigLayer]:
    """Resolve the Packaged, Global, User, and Repo config layers present on disk, in precedence order."""
    layers = [ConfigLayer(tier=ConfigTier.PACKAGED, path=None, data=_packaged_defaults())]

    tier_paths = (
        (ConfigTier.GLOBAL, paths.global_paths.global_dir / "config.json"),
        (ConfigTier.USER, paths.global_paths.user_dir / "config.json"),
        (ConfigTier.REPO, paths.config_file),
    )
    for tier, path in tier_paths:
        data = _read_tier_file(tier, path)
        if data is not None:
            layers.append(ConfigLayer(tier=tier, path=path, data=data))

    return layers


def _classify_tier_error(details: str) -> HierarchicalConfigLoadStatus:
    """Map a ConfigTierValidationError's details text to a load-result status."""
    if "Check file permissions and that the path is readable" in details:
        return HierarchicalConfigLoadStatus.UNREADABLE
    if details == "root must be a JSON object":
        return HierarchicalConfigLoadStatus.ROOT_NOT_OBJECT
    if "line" in details and "column" in details:
        return HierarchicalConfigLoadStatus.MALFORMED_JSON
    return HierarchicalConfigLoadStatus.VALIDATION_FAILED


def load_hierarchical_config(paths: WorkspacePaths) -> HierarchicalConfigLoadResult:
    """Merge Packaged, Global, User, and Repo config tiers into one validated DovoConfig, without raising."""
    try:
        layers = resolve_config_layers(paths)

        merged = _deep_merge({}, layers[0].data)
        for layer in layers[1:]:
            merged = _deep_merge(merged, layer.data)
            if layer.path is None:
                continue
            _validate_merged_layer(layer.tier, layer.path, merged)

        merged["environment"]["sensitive_variables"] = _union_sensitive_variables(layers)
        config = DovoConfig.model_validate(merged)
    except ConfigTierValidationError as exc:
        return HierarchicalConfigLoadResult(
            status=_classify_tier_error(exc.details),
            tier=exc.tier,
            path=exc.path,
            errors=[str(exc)],
        )

    return HierarchicalConfigLoadResult(status=HierarchicalConfigLoadStatus.OK, config=config)
