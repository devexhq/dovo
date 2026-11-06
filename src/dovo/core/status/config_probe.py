"""Configuration file probe for workspace status."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config import Config
from dovo.core.status.models import ConfigStatusInfo


def probe_config(paths: WorkspacePaths) -> ConfigStatusInfo:
    """Load config.json read-only and return its load error code, parsed model, errors, remediations and the load's records."""
    result = Config(paths).load()
    return ConfigStatusInfo(
        error_code=result.error_code,
        config_path=result.config_path,
        is_valid=result.ok,
        raw=result.raw,
        config=result.config,
        errors=result.errors,
        remediations=result.remediations,
        diagnostics=result.diagnostics,
    )
