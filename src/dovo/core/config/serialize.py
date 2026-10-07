"""Serialize normalized config for display and tooling."""

from __future__ import annotations

import json
from typing import Any

from dovo.core.config.models import DovoConfig

# Normative top-level order. Nested order follows model fields.
_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "version",
    "project",
    "worktree",
    "agent",
    "history",
    "doctor",
    "prune",
    "telemetry",
    "concurrency",
    "environment",
)


def serialize_config(config: DovoConfig) -> dict[str, Any]:
    """Return the full normalized config as a plain dict.

    Does not read the filesystem, print, exit, or mutate ``config``. Values
    include model defaults and load-time normalizations (e.g. project name).

    Args:
        config: Loaded, validated Dovo configuration.

    Returns:
        JSON-ready dict with deterministic key order.
    """
    dumped = config.model_dump(mode="json")
    return {key: dumped[key] for key in _TOP_LEVEL_KEYS}


def as_json(config: DovoConfig) -> str:
    """Return pretty-printed JSON text (2-space indent, trailing newline).

    Args:
        config: Loaded, validated Dovo configuration.

    Returns:
        Parseable JSON text ending with a newline.
    """
    return (
        json.dumps(
            serialize_config(config),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
