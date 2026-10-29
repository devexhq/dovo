"""Workspace initialization service."""

from __future__ import annotations

from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import Failure, catch_failure
from dovo.common.filesystem import Filesystem
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.models import Remediation
from dovo.common.utils import display_path
from dovo.core.bootstrap.models import BootstrapSummary, DovoTreeSnapshot, WorkspaceInitResult
from dovo.core.bootstrap.services.bootstrap import bootstrap_dovo
from dovo.core.bootstrap.services.rollback import restore_dovo_tree, snapshot_dovo_tree
from dovo.core.catalog.models import SeedSummary
from dovo.core.catalog.services.seeder import seed_all_catalog_templates
from dovo.core.config.generator import generate_default_config
from dovo.core.config.models import ConfigGenerationSummary
from dovo.core.db import init_database
from dovo.core.project.models import ProvisionedProjectIdentity
from dovo.core.project.services.identity import provision_project_identity, validate_project_id
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

    Refuses a target whose .dovo is the global Dovo home, performs git
    preflight, validates --id, bootstraps the .dovo/ directory tree, provisions
    the project identity, generates canonical default configuration, initializes
    the SQLite state database, and seeds starter catalog templates. A failed step
    is reported by its error_code, with the summaries of the steps before it.
    """
    fs = Filesystem(root)

    refusal = _refuse_global_home(fs)
    if refusal is not None:
        return refusal

    if not fs.is_git_repo():
        err = (
            "The current directory is not a valid Git repository.\n"
            "Run [bold cyan]git init[/bold cyan] before running [bold cyan]dovo init[/bold cyan]."
        )
        return WorkspaceInitResult(error_code=ErrorCode.BOOTSTRAP_REPO_NOT_FOUND, errors=[err])

    bootstrap: BootstrapSummary | None = None
    provisioned: ProvisionedProjectIdentity | None = None
    config_generation: ConfigGenerationSummary | None = None
    seed: SeedSummary | None = None
    with catch_failure("bootstrap.initialize", path=str(fs.dovo_dir)) as caught:
        if project_id is not None:
            validate_project_id(project_id)
        bootstrap = bootstrap_dovo(fs.dovo_dir, tool_version=tool_version)
        provisioned = provision_project_identity(
            fs.dovo_dir, project_id=project_id, display_name=display_name, force=force
        )
        config_generation = generate_default_config(
            fs.config_file, project_name=fs.root_dir.name, overwrite=overwrite, repair=repair
        )
        paths = build_workspace_paths(fs.repository_paths, resolve_global_paths(), provisioned.identity.id)
        init_database(paths.database_file)
        seed = seed_all_catalog_templates(paths)

    return _build_init_result(
        fs.dovo_dir / "project.json", bootstrap, provisioned, config_generation, seed, caught.failure
    )


def _build_init_result(
    identity_path: Path,
    bootstrap: BootstrapSummary | None,
    provisioned: ProvisionedProjectIdentity | None,
    config_generation: ConfigGenerationSummary | None,
    seed: SeedSummary | None,
    failure: Failure | None,
) -> WorkspaceInitResult:
    """Build the init result from the steps that completed, copying failure into it when a step failed."""
    warnings: list[str] = []
    if provisioned is not None:
        identity_path = provisioned.path
        if provisioned.warning is not None:
            warnings.append(provisioned.warning)
    if config_generation is not None:
        warnings.extend(config_generation.warnings)

    return WorkspaceInitResult(
        bootstrap=bootstrap,
        project_identity=provisioned.identity if provisioned is not None else None,
        identity_path=identity_path if bootstrap is not None else None,
        identity_outcome=provisioned.outcome if provisioned is not None else None,
        config_generation=config_generation,
        seed=seed,
        warnings=warnings,
        **(failure.result_fields() if failure is not None else {}),
    )


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
        error_code=ErrorCode.BOOTSTRAP_TARGET_FORBIDDEN,
    )
