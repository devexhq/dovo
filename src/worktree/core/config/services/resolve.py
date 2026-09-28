"""Resolve the effective configuration Config and wt config show both read."""

from __future__ import annotations

from worktree.common.filesystem import WorkspacePaths
from worktree.core.config.loader import ConfigLoadResult, ConfigLoadStatus, load_config
from worktree.core.config.services.hierarchical_loader import load_hierarchical_config


def resolve_effective_config(paths: WorkspacePaths) -> ConfigLoadResult:
    """Load the repo tier, then merge Global and User tier overrides on top; never raises."""
    repo_result = load_config(paths)
    if not repo_result.ok:
        return repo_result

    hierarchical = load_hierarchical_config(paths)

    if not hierarchical.ok or hierarchical.config is None:
        return ConfigLoadResult(
            status=ConfigLoadStatus.TIER_INVALID,
            config_path=hierarchical.path or repo_result.config_path,
            errors=list(hierarchical.errors),
        )

    return ConfigLoadResult(
        status=ConfigLoadStatus.OK,
        config_path=repo_result.config_path,
        raw=hierarchical.config.model_dump(mode="json"),
        config=hierarchical.config,
        errors=[],
    )
