"""Unified workspace health and runtime status collector."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import Filesystem, WorkspacePaths
from dovo.core.config import Config, ConfigLoadStatus
from dovo.core.db import (
    RunsRepository,
    WorktreesRepository,
    WorktreeStatus,
)
from dovo.core.git import (
    GitCommandError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
    GitRunner,
)
from dovo.core.status.models import (
    CatalogStatusInfo,
    ConfigStatusInfo,
    DatabaseStatusInfo,
    DovoStatusResult,
    GitStatusInfo,
    WorktreeStatusInfo,
)


def _collect_git_status(root_dir: Path) -> GitStatusInfo:
    """Collect git repository branch, dirty status, and uncommitted file count."""
    try:
        is_inside = GitRunner.run(["rev-parse", "--is-inside-work-tree"], path=root_dir).strip()
        if is_inside != "true":
            return GitStatusInfo(
                is_git_repo=False,
                branch="none",
                is_dirty=False,
                uncommitted_files=0,
            )
        branch = GitRunner.get_current_branch(root_dir)
        status_lines = GitRunner.status_porcelain(root_dir)
        return GitStatusInfo(
            is_git_repo=True,
            branch=branch,
            is_dirty=bool(status_lines),
            uncommitted_files=len(status_lines),
        )
    except (GitNotFoundError, GitPlumbingTimeoutError):
        return GitStatusInfo(
            is_git_repo=False,
            branch="unknown",
            is_dirty=False,
            uncommitted_files=0,
        )
    except GitCommandError:
        return GitStatusInfo(
            is_git_repo=False,
            branch="none",
            is_dirty=False,
            uncommitted_files=0,
        )


def _collect_config_status(paths: WorkspacePaths) -> ConfigStatusInfo:
    """Collect configuration file status without mutations."""
    result = Config(paths).load()
    return ConfigStatusInfo(
        status=result.status,
        config_path=result.config_path,
        is_valid=result.ok,
        raw=result.raw,
        config=result.config,
        errors=result.errors,
        fixes=result.fixes,
    )


def _scan_catalog_category(category_dir: Path) -> tuple[int, int, list[str]]:
    """Scan a catalog category directory, returning (total_count, invalid_count, item_names)."""
    if not category_dir.is_dir():
        return 0, 0, []

    entries = Filesystem.scan_yaml_directory(category_dir)
    invalid_count = 0
    names: list[str] = []

    for entry in entries:
        names.append(entry.name)
        if entry.error is not None or entry.parsed is None or not isinstance(entry.parsed, dict):
            invalid_count += 1

    return len(entries), invalid_count, names


def _collect_catalog_status(catalog_dir: Path) -> CatalogStatusInfo:
    """Collect blueprint catalog directory health and item counts."""
    if not catalog_dir.is_dir():
        return CatalogStatusInfo(
            exists=False,
            catalog_dir=catalog_dir,
            total_items=0,
            workflows_count=0,
            tasks_count=0,
            steps_count=0,
            invalid_items=0,
            item_names=[],
        )

    blueprints_count, invalid_blueprints, blueprint_names = _scan_catalog_category(catalog_dir / "blueprints")
    steps_count, invalid_steps, step_names = _scan_catalog_category(catalog_dir / "steps")

    total_items = blueprints_count + steps_count
    invalid_items = invalid_blueprints + invalid_steps
    item_names = [*blueprint_names, *step_names]

    return CatalogStatusInfo(
        exists=True,
        catalog_dir=catalog_dir,
        total_items=total_items,
        workflows_count=0,
        tasks_count=0,
        steps_count=steps_count,
        invalid_items=invalid_items,
        item_names=item_names,
    )


def _collect_database_status(paths: WorkspacePaths) -> DatabaseStatusInfo:
    """Collect centralized SQLite database accessibility and total recorded runs."""
    db_path = paths.database_file
    if not db_path.is_file():
        return DatabaseStatusInfo(
            exists=False,
            db_path=db_path,
            is_accessible=False,
            total_runs=0,
        )

    try:
        runs_repo = RunsRepository(db_path=db_path, project_id=paths.project_id, auto_init=False)
        total_runs = len(runs_repo.list())
        return DatabaseStatusInfo(
            exists=True,
            db_path=db_path,
            is_accessible=True,
            total_runs=total_runs,
        )
    except Exception:
        return DatabaseStatusInfo(
            exists=True,
            db_path=db_path,
            is_accessible=False,
            total_runs=0,
        )


def _collect_worktree_status(
    paths: WorkspacePaths,
    config_status: ConfigStatusInfo,
    database_status: DatabaseStatusInfo,
) -> WorktreeStatusInfo:
    """Collect active and total worktrees with configured concurrency limits."""
    max_active_worktrees = (
        config_status.config.worktree.max_active_worktrees
        if (config_status.is_valid and config_status.config is not None)
        else 5
    )

    worktrees_dir = paths.worktrees_dir

    if database_status.is_accessible:
        try:
            worktrees_repo = WorktreesRepository(
                db_path=paths.database_file, project_id=paths.project_id, auto_init=False
            )
            active_worktrees = len(worktrees_repo.list(status=WorktreeStatus.ACTIVE))
            total_worktrees = len(worktrees_repo.list())
            return WorktreeStatusInfo(
                active_worktrees=active_worktrees,
                total_worktrees=total_worktrees,
                max_active_worktrees=max_active_worktrees,
            )
        except Exception:
            pass

    if worktrees_dir.is_dir():
        dir_count = len([path for path in worktrees_dir.iterdir() if path.is_dir()])
        return WorktreeStatusInfo(
            active_worktrees=dir_count,
            total_worktrees=dir_count,
            max_active_worktrees=max_active_worktrees,
        )

    return WorktreeStatusInfo(
        active_worktrees=0,
        total_worktrees=0,
        max_active_worktrees=max_active_worktrees,
    )


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

    if config.config is not None and not config.config.agent.model:
        warnings.append("Agent model is not configured (agent.model is null).")

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


def collect_status(paths: WorkspacePaths) -> DovoStatusResult:
    """Collect workspace health and runtime status without side effects."""
    git_status = _collect_git_status(paths.root_dir)
    config_status = _collect_config_status(paths)
    catalog_status = _collect_catalog_status(paths.catalog_dir)
    database_status = _collect_database_status(paths)
    worktree_status = _collect_worktree_status(paths, config_status, database_status)
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
