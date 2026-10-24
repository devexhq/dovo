"""Status domain entrypoint."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config import ConfigLoadStatus, agent_settings
from dovo.core.git import GitRunner
from dovo.core.status.catalog_probe import probe_catalog
from dovo.core.status.config_probe import probe_config
from dovo.core.status.database_probe import probe_database
from dovo.core.status.git_probe import probe_git
from dovo.core.status.models import (
    CatalogStatusInfo,
    ConfigStatusInfo,
    DovoStatusResult,
    GitStatusInfo,
    WorktreeStatusInfo,
)
from dovo.core.status.worktree_probe import probe_worktrees


def _max_active_worktrees(config: ConfigStatusInfo) -> int:
    """Return the configured worktree concurrency limit, or 5 when config is invalid or absent."""
    return config.config.worktree.max_active_worktrees if (config.is_valid and config.config is not None) else 5


def _clean_error_message(error: str) -> str:
    """Extract a concise single-line warning message from a raw error string.

    Args:
        error: Raw multi-line error string from config loader.

    Returns:
        Sanitized single-line error message without file paths.
    """
    first_line = error.split("\n")[0].strip()
    if "at '" not in first_line:
        return first_line
    prefix, _, rest = first_line.partition("at '")
    _, _, message = rest.partition("': ")
    return f"{prefix.strip()}: {message.strip()}" if message else first_line


def _collect_config_error_warnings(config: ConfigStatusInfo) -> list[str]:
    """Extract sanitized warning messages from config loading errors.

    Args:
        config: Collected configuration status information.

    Returns:
        List of cleaned warning strings extracted from config errors.
    """
    if config.status in (ConfigLoadStatus.OK, ConfigLoadStatus.NOT_FOUND):
        return []

    warnings: list[str] = []
    for error in config.errors:
        clean_message = _clean_error_message(error)
        if clean_message and clean_message not in warnings:
            warnings.append(clean_message)
    return warnings


def _collect_warnings(
    *,
    git: GitStatusInfo,
    config: ConfigStatusInfo,
    catalog: CatalogStatusInfo,
    worktrees: WorktreeStatusInfo,
) -> list[str]:
    """Aggregate actionable developer warnings in deterministic order."""
    warnings: list[str] = []

    if config.status == ConfigLoadStatus.NOT_FOUND:
        warnings.append("Dovo workspace is not initialized. Run 'dovo init' to configure.")

    if git.branch in ("main", "master"):
        warnings.append(f"Active branch is '{git.branch}'. Automated workflows on primary branches are discouraged.")

    if git.is_dirty:
        warnings.append(f"Working tree has {git.uncommitted_files} uncommitted change(s).")

    if config.config is not None and not agent_settings(config.config).model:
        warnings.append(f"Agent model is not configured (agents.{config.config.default_agent}.model is unset).")

    if worktrees.max_active_worktrees > 5:
        warnings.append(f"max_active_worktrees ({worktrees.max_active_worktrees}) is unusually high.")

    if catalog.invalid_items > 0:
        warnings.append(f"{catalog.invalid_items} invalid blueprint file(s) detected in catalog.")

    warnings.extend(_collect_config_error_warnings(config))

    return warnings


CONFIG_REMEDIATION_MAP: dict[ConfigLoadStatus, str] = {
    ConfigLoadStatus.NOT_FOUND: "Run 'dovo init' to initialize Dovo in this repository.",
    ConfigLoadStatus.MALFORMED_JSON: "Repair JSON syntax in .dovo/config.json or restore from backup.",
    ConfigLoadStatus.SCHEMA_INVALID: (
        "Run 'dovo config validate' to inspect schema errors or 'dovo init --repair' to insert missing keys."
    ),
    ConfigLoadStatus.ROOT_NOT_OBJECT: "Ensure .dovo/config.json contains a JSON object root.",
    ConfigLoadStatus.PATH_IS_DIRECTORY: "Remove directory at .dovo/config.json and run 'dovo init'.",
    ConfigLoadStatus.UNREADABLE: "Check file permissions for .dovo/config.json.",
}


def _collect_fixes(
    *,
    git: GitStatusInfo,
    config: ConfigStatusInfo,
) -> list[str]:
    """Aggregate actionable remediation command hints in deterministic order.

    Args:
        git: Git repository status information.
        config: Configuration status information.

    Returns:
        List of suggested remediation action strings.
    """
    fixes: list[str] = []
    if config.status in CONFIG_REMEDIATION_MAP:
        fixes.append(CONFIG_REMEDIATION_MAP[config.status])
    if not git.is_git_repo:
        fixes.append("Run 'git init' or navigate to a Git repository.")
    return fixes


class Status:
    """Unified entrypoint for workspace status and runtime health collection."""

    def __init__(self, paths: WorkspacePaths, *, git: GitRunner) -> None:
        """Bind this Status instance to a resolved WorkspacePaths snapshot and the git runner its probe uses."""
        self.paths = paths
        self.git = git

    def collect(self) -> DovoStatusResult:
        """Probe git, config, catalog, database and worktrees, then derive warnings, fixes and initialization."""
        paths = self.paths
        git_status = probe_git(paths.root_dir, git=self.git)
        config_status = probe_config(paths)
        catalog_status = probe_catalog(paths.catalog_dir)
        database_status = probe_database(paths)
        worktree_status = probe_worktrees(
            paths,
            max_active_worktrees=_max_active_worktrees(config_status),
            query_database=database_status.is_accessible,
        )
        warnings = _collect_warnings(
            git=git_status,
            config=config_status,
            catalog=catalog_status,
            worktrees=worktree_status,
        )
        fixes = _collect_fixes(
            git=git_status,
            config=config_status,
        )

        is_initialized = paths.dovo_dir.is_dir() and config_status.status != ConfigLoadStatus.NOT_FOUND

        return DovoStatusResult(
            root_dir=paths.root_dir,
            is_initialized=is_initialized,
            git=git_status,
            config=config_status,
            catalog=catalog_status,
            database=database_status,
            worktrees=worktree_status,
            warnings=warnings,
            fixes=fixes,
        )
