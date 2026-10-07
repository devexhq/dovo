"""Project-aware workspace runtime storage resolution."""

from __future__ import annotations

from dovo.common.filesystem.exceptions import WorkspaceNotInitializedError
from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.paths import get_catalog_templates_dir
from dovo.core.project.services.identity import load_project_identity


def build_workspace_paths(
    repository_paths: RepositoryPaths, global_paths: GlobalPaths, project_id: str
) -> WorkspacePaths:
    """Construct the invocation's paths for an initialized project; performs no I/O."""
    runtime_root = global_paths.storage_dir / "projects" / project_id

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


def resolve_workspace_paths(repository_paths: RepositoryPaths, global_paths: GlobalPaths) -> WorkspacePaths:
    """Load the project identity once and build the invocation's paths; raises WorkspaceNotInitializedError when it is missing or unusable."""
    identity_path = repository_paths.dovo_dir / "project.json"
    identity_result = load_project_identity(identity_path)
    if not identity_result.ok or identity_result.identity is None:
        raise WorkspaceNotInitializedError(identity_path, identity_present=identity_path.is_file())

    return build_workspace_paths(repository_paths, global_paths, identity_result.identity.id)
