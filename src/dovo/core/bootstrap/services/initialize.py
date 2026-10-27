"""Workspace initialization service."""

from __future__ import annotations

from pathlib import Path

from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.models import Remediation
from dovo.common.utils import display_path
from dovo.core.bootstrap.models import (
    BootstrapResult,
    DovoTreeSnapshot,
    InitFailureMode,
    WorkspaceInitResult,
)
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo
from dovo.core.bootstrap.services.rollback import restore_dovo_tree, snapshot_dovo_tree
from dovo.core.catalog.services.seeder import seed_all_catalog_templates
from dovo.core.config.generator import generate_default_config
from dovo.core.db import init_database
from dovo.core.project.models import ProvisionedProjectIdentity
from dovo.core.project.services.identity import provision_project_identity, validate_project_id
from dovo.core.project.services.storage import build_workspace_paths

_BOOTSTRAP_REMEDIATION = Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")


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

    Refuses a target whose .dovo is the global Dovo home, performs git
    preflight, bootstraps the .dovo/ directory tree, provisions
    the project identity, generates canonical default configuration, initializes
    the SQLite state database, and seeds starter catalog templates.
    """
    fs = Filesystem(root)
    resolved_root = fs.root_dir

    refusal = _refuse_global_home(fs)
    if refusal is not None:
        return refusal

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
            remediations=[_BOOTSTRAP_REMEDIATION],
            failure_mode=InitFailureMode.BOOTSTRAP,
        )

    provisioned = _provision_identity(
        fs.dovo_dir, result, project_id=project_id, display_name=display_name, force=force
    )
    if isinstance(provisioned, WorkspaceInitResult):
        return provisioned
    identity_warnings = [provisioned.warning] if provisioned.warning is not None else []

    config_result = generate_default_config(
        fs.config_file,
        project_name=resolved_root.name,
        overwrite=overwrite,
        repair=repair,
    )
    if not config_result.ok:
        return WorkspaceInitResult(
            bootstrap_result=result,
            project_identity=provisioned.identity,
            identity_path=provisioned.path,
            identity_outcome=provisioned.outcome,
            config_result=config_result,
            errors=list(config_result.errors),
            warnings=[*identity_warnings, *config_result.warnings],
            failure_mode=InitFailureMode.CONFIG_GENERATION,
        )

    paths = build_workspace_paths(fs.repository_paths, resolve_global_paths(), provisioned.identity.id)
    init_database(paths.database_file)

    seed_result = seed_all_catalog_templates(paths)
    return WorkspaceInitResult(
        bootstrap_result=result,
        project_identity=provisioned.identity,
        identity_path=provisioned.path,
        identity_outcome=provisioned.outcome,
        config_result=config_result,
        seed_result=seed_result,
        errors=list(seed_result.errors),
        warnings=[*identity_warnings, *config_result.warnings, *seed_result.warnings],
        failure_mode=None,
    )


def _provision_identity(
    dovo_dir: Path,
    bootstrap_result: BootstrapResult,
    *,
    project_id: str | None,
    display_name: str | None,
    force: bool,
) -> ProvisionedProjectIdentity | WorkspaceInitResult:
    """Validate --id, then provision the identity, each in its own catch_failure block; return the identity or the init failure."""
    identity_path = dovo_dir / "project.json"
    if project_id is not None:
        with catch_failure("bootstrap.initialize_project_id", path=str(identity_path)) as caught:
            validate_project_id(project_id)
        if caught.failure is not None:
            return WorkspaceInitResult(
                bootstrap_result=bootstrap_result,
                identity_path=identity_path,
                failure_mode=InitFailureMode.INVALID_PROJECT_ID,
                **caught.failure.result_fields(),
            )

    with catch_failure("bootstrap.initialize", path=str(identity_path)) as caught:
        provisioned = provision_project_identity(
            dovo_dir, project_id=project_id, display_name=display_name, force=force
        )
    if caught.failure is not None:
        fields = caught.failure.result_fields()
        fields["remediations"] = [_BOOTSTRAP_REMEDIATION]
        return WorkspaceInitResult(
            bootstrap_result=bootstrap_result,
            identity_path=identity_path,
            failure_mode=InitFailureMode.BOOTSTRAP,
            **fields,
        )
    return provisioned


def auto_initialize_workspace(root: Path | None = None) -> WorkspaceInitResult:
    """Initialize the workspace for `dovo run`; on a failure after preflight, restore the project's .dovo to its state before the attempt.

    A returned failure carries any rollback error in its errors. An exception
    raised by initialization is re-raised after the rollback, with any rollback
    error added as a note.
    """
    fs = Filesystem(root)
    if _refuse_global_home(fs) is not None or not fs.is_git_repo():
        return initialize_workspace(root)

    snapshot = snapshot_dovo_tree(fs.dovo_dir)
    try:
        result = initialize_workspace(root)
    except BaseException as exc:
        rollback_error = _rollback(snapshot)
        if rollback_error is not None:
            exc.add_note(rollback_error)
        raise
    if result.ok:
        return result

    rollback_error = _rollback(snapshot)
    if rollback_error is not None:
        return result.model_copy(update={"errors": [*result.errors, rollback_error]})
    return result


def _rollback(snapshot: DovoTreeSnapshot) -> str | None:
    """Restore snapshot's .dovo tree; return the error to report when that fails, else None."""
    try:
        restore_dovo_tree(snapshot)
    except OSError as exc:
        return (
            f"Could not roll back the partial workspace at {display_path(snapshot.dovo_dir)}: {exc}. "
            "Remove it, then rerun dovo run."
        )
    return None


def _refuse_global_home(fs: Filesystem) -> WorkspaceInitResult | None:
    """Return a preflight failure when fs's .dovo directory resolves to the global Dovo home, else None."""
    global_root = resolve_global_paths().root
    if fs.dovo_dir.resolve() != global_root:
        return None
    return WorkspaceInitResult(
        errors=[
            f"Cannot initialize a workspace at '{fs.root_dir}': its .dovo directory is the global Dovo home "
            f"'{global_root}'."
        ],
        remediations=[
            Remediation(message="Run 'dovo init' inside a project directory"),
            Remediation(message="Set 'DOVO_HOME' to a different directory."),
        ],
        failure_mode=InitFailureMode.PREFLIGHT,
    )
