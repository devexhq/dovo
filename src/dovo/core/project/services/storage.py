"""Project-aware workspace runtime storage resolution."""

from __future__ import annotations

from dovo.common.diagnostic_log import DiagnosticLevel, record_exception
from dovo.common.exceptions import workspace_not_initialized_error
from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.paths import get_catalog_templates_dir
from dovo.core.project.exceptions import ProjectIdentityError
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
    """Load the project identity once and build the invocation's paths.

    Raises WorkspaceError with WORKSPACE_NOT_INITIALIZED when the identity is missing or unusable, carrying the
    record of an OSError that prevented reading it, or when the .dovo directory is the global Dovo home, whose files
    are never read as workspace files.
    """
    identity_path = repository_paths.dovo_dir / "project.json"
    if repository_paths.dovo_dir.resolve() == global_paths.root:
        raise workspace_not_initialized_error(identity_path, identity_present=False, diagnostic=None)
    try:
        identity = load_project_identity(identity_path)
    except ProjectIdentityError as error:
        diagnostic = (
            record_exception(
                error.__cause__,
                level=DiagnosticLevel.ERROR,
                operation="project.resolve_workspace_paths",
                context={"path": str(identity_path)},
            )
            if isinstance(error.__cause__, OSError)
            else None
        )
        raise workspace_not_initialized_error(
            identity_path, identity_present=identity_path.is_file(), diagnostic=diagnostic
        ) from error.__cause__

    return build_workspace_paths(repository_paths, global_paths, identity.id)
