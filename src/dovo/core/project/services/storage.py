"""Project-aware workspace runtime storage resolution."""

from __future__ import annotations

from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.paths import get_catalog_templates_dir
from dovo.core.project.services.identity import load_project_identity


def resolve_workspace_paths(repository_paths: RepositoryPaths, global_paths: GlobalPaths) -> WorkspacePaths:
    """Load project identity once and construct the invocation's final paths."""
    identity_result = load_project_identity(repository_paths.dovo_dir / "project.json")
    project_id = identity_result.identity.id if identity_result.ok and identity_result.identity is not None else None
    runtime_root = (
        global_paths.storage_dir / "projects" / project_id if project_id is not None else repository_paths.dovo_dir
    )

    return WorkspacePaths(
        root_dir=repository_paths.root_dir,
        dovo_dir=repository_paths.dovo_dir,
        config_file=repository_paths.config_file,
        catalog_dir=repository_paths.catalog_dir,
        catalog_steps_dir=repository_paths.catalog_steps_dir,
        catalog_blueprints_dir=repository_paths.catalog_blueprints_dir,
        worktrees_dir=repository_paths.worktrees_dir,
        lock_file=repository_paths.lock_file,
        gitignore_file=repository_paths.gitignore_file,
        catalog_templates_dir=get_catalog_templates_dir(),
        global_paths=global_paths,
        database_file=global_paths.data_dir / "dovo.db",
        project_id=project_id,
        runtime_root=runtime_root,
        logs_dir=runtime_root / "logs",
        sessions_dir=runtime_root / "sessions",
        artifacts_dir=runtime_root / "artifacts",
        tmp_dir=runtime_root / "tmp",
    )
