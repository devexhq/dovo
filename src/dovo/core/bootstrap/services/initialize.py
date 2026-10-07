"""Workspace initialization service."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.bootstrap.models import (
    InitFailureMode,
    WorkspaceInitResult,
)
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo
from dovo.core.catalog.services.seeder import seed_all_catalog_templates
from dovo.core.config.generator import generate_default_config
from dovo.core.db import init_database
from dovo.core.project.services.identity import provision_project_identity
from dovo.core.project.services.storage import build_workspace_paths


def initialize_workspace(
    root: Path | None = None,
    *,
    tool_version: str | None = None,
    overwrite: bool = False,
    repair: bool = False,
    project_id: str | None = None,
    display_name: str | None = None,
    force: bool = False,
) -> WorkspaceInitResult:
    """Initialize a local project workspace for Dovo CLI and desktop sync.

    Performs git preflight, bootstraps the .dovo/ directory tree, provisions
    the project identity, generates canonical default configuration, initializes
    the SQLite state database, and seeds starter catalog templates.
    """
    fs = Filesystem(root)
    resolved_root = fs.root_dir

    if not fs.is_git_repo():
        err = (
            "The current directory is not a valid Git repository.\n"
            "Run [bold cyan]git init[/bold cyan] before running [bold cyan]dovo init[/bold cyan]."
        )
        return WorkspaceInitResult(errors=[err], failure_mode=InitFailureMode.PREFLIGHT)

    result = bootstrap_dovo(fs.dovo_dir, tool_version=tool_version)
    if not result.ok:
        return WorkspaceInitResult(
            bootstrap_result=result,
            errors=list(result.errors),
            failure_mode=InitFailureMode.BOOTSTRAP,
        )

    identity_result = provision_project_identity(
        fs.dovo_dir,
        project_id=project_id,
        display_name=display_name,
        force=force,
    )
    if not identity_result.ok or identity_result.identity is None:
        return WorkspaceInitResult(
            bootstrap_result=result,
            identity_result=identity_result,
            errors=list(identity_result.errors),
        )

    config_result = generate_default_config(
        fs.config_file,
        project_name=resolved_root.name,
        overwrite=overwrite,
        repair=repair,
    )
    if not config_result.ok:
        return WorkspaceInitResult(
            bootstrap_result=result,
            identity_result=identity_result,
            config_result=config_result,
            errors=list(config_result.errors),
            failure_mode=InitFailureMode.CONFIG_GENERATION,
        )

    paths = build_workspace_paths(fs.repository_paths, resolve_global_paths(), identity_result.identity.id)
    init_database(paths.database_file)

    seed_result = seed_all_catalog_templates(paths)
    return WorkspaceInitResult(
        bootstrap_result=result,
        identity_result=identity_result,
        config_result=config_result,
        seed_result=seed_result,
        errors=list(seed_result.errors),
        failure_mode=None,
    )
