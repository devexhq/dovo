"""Status domain entrypoint."""

from __future__ import annotations

from typing import Final

from dovo.common.diagnostic_log import error_diagnostics
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.core.config import agent_settings
from dovo.core.db import SessionsRepository
from dovo.core.git import GitRunner
from dovo.core.sessions import reconcile_stale_sessions
from dovo.core.status.catalog_probe import probe_catalog
from dovo.core.status.config_probe import probe_config
from dovo.core.status.database_probe import probe_database
from dovo.core.status.git_probe import probe_git
from dovo.core.status.models import (
    CatalogStatusInfo,
    ConfigStatusInfo,
    DatabaseStatusInfo,
    DovoStatusResult,
    GitStatusInfo,
    WorkspaceHealth,
    WorktreeStatusInfo,
)
from dovo.core.status.worktree_probe import probe_worktrees

_GIT_INIT_REMEDIATION: Final = Remediation(
    message="Run 'git init' or navigate to a Git repository.", command="git init"
)
_GIT_UNREADABLE_REMEDIATIONS: Final[dict[ErrorCode, Remediation]] = {
    ErrorCode.GIT_TIMEOUT: Remediation(
        message=(
            "Check for git lock files, credential prompts, or stuck git processes, then retry, "
            "or raise 'DOVO_GIT_TIMEOUT_SECONDS' for a slow repository."
        )
    ),
    ErrorCode.GIT_BINARY_MISSING: Remediation(message="Install Git and make sure 'git' is on PATH."),
}


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
    if config.error_code in (None, ErrorCode.CONFIG_NOT_FOUND):
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

    if config.error_code == ErrorCode.CONFIG_NOT_FOUND:
        warnings.append("Dovo workspace is not initialized. Run 'dovo init' to configure.")

    warnings.extend(warning for warning in (git.warning, worktrees.warning) if warning)

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


def _collect_remediations(
    *,
    git: GitStatusInfo,
    config: ConfigStatusInfo,
) -> list[Remediation]:
    """Aggregate the config load remediations in load order, then the git step.

    The git step fixes the code git could not be read under, or runs 'git init' when git says the directory is not
    a repository.

    Args:
        git: Git repository status information.
        config: Configuration status information.

    Returns:
        List of remediation steps.
    """
    remediations: list[Remediation] = []
    remediations.extend(config.remediations)
    if git.error_code is not None:
        remediations.append(_GIT_UNREADABLE_REMEDIATIONS[git.error_code])
    elif not git.is_git_repo:
        remediations.append(_GIT_INIT_REMEDIATION)
    return remediations


def _classify_health(
    *,
    is_initialized: bool,
    git: GitStatusInfo,
    config: ConfigStatusInfo,
    catalog: CatalogStatusInfo,
    database: DatabaseStatusInfo,
) -> WorkspaceHealth:
    """Classify workspace health: uninitialized without .dovo/ or config.json, degraded when any probe is unhealthy.

    Args:
        is_initialized: Whether .dovo/ exists and config.json was found.
        git: Git repository status information.
        config: Configuration status information.
        catalog: Catalog status information.
        database: Database status information.

    Returns:
        The workspace health classification.
    """
    if not is_initialized:
        return WorkspaceHealth.UNINITIALIZED
    healthy = git.is_git_repo and config.is_valid and database.is_accessible and catalog.invalid_items == 0
    return WorkspaceHealth.OK if healthy else WorkspaceHealth.DEGRADED


class Status:
    """Unified entrypoint for workspace status and runtime health collection."""

    def __init__(self, paths: WorkspacePaths, *, git: GitRunner, sessions_db: SessionsRepository) -> None:
        """Bind this Status to a resolved WorkspacePaths snapshot, the git runner its probe uses and the sessions it reconciles."""
        self.paths = paths
        self.git = git
        self.sessions_db = sessions_db

    def collect(self) -> DovoStatusResult:
        """Reconcile stale sessions, then probe git, config, catalog, database and worktrees and derive warnings, remediations, initialization and health.

        diagnostics lists the records of the reconciliation, git, config, catalog, database and worktree failures caught,
        in that order.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        paths = self.paths
        reconciliation = reconcile_stale_sessions(self.sessions_db, path=paths.root_dir)
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
        if reconciliation.warning and reconciliation.warning not in warnings:
            warnings.insert(0, reconciliation.warning)
        remediations = _collect_remediations(
            git=git_status,
            config=config_status,
        )

        is_initialized = paths.dovo_dir.is_dir() and config_status.error_code != ErrorCode.CONFIG_NOT_FOUND

        return DovoStatusResult(
            status=_classify_health(
                is_initialized=is_initialized,
                git=git_status,
                config=config_status,
                catalog=catalog_status,
                database=database_status,
            ),
            root_dir=paths.root_dir,
            is_initialized=is_initialized,
            git=git_status,
            config=config_status,
            catalog=catalog_status,
            database=database_status,
            worktrees=worktree_status,
            warnings=warnings,
            remediations=remediations,
            diagnostics=[
                *error_diagnostics(reconciliation.diagnostic),
                *error_diagnostics(git_status.diagnostic),
                *config_status.diagnostics,
                *catalog_status.diagnostics,
                *error_diagnostics(database_status.diagnostic),
                *error_diagnostics(worktree_status.diagnostic),
            ],
        )
