"""Project identity creation and persistence services."""

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from dovo.common.filesystem.filesystem import Filesystem
from dovo.common.lock import LockTimeoutError, WorkspaceLock, resolve_lock_file_path
from dovo.core.project.models import (
    PROJECT_ID_REGEX,
    ProjectIdentity,
    ProjectIdentityError,
    ProjectIdentityErrorType,
    ProjectIdentityLoadResult,
    ProjectIdentityLoadStatus,
    ProjectIdentityProvisionResult,
    ProjectIdentityProvisionStatus,
    ProjectIdentitySaveResult,
    ProjectIdentitySaveStatus,
)
from dovo.core.project.services.slug import generate_slug


def generate_project_identity(
    project_id: str | None = None,
    display_name: str | None = None,
) -> ProjectIdentity:
    """Create a validated project identity using a supplied or generated identifier."""
    return ProjectIdentity(
        id=generate_slug() if project_id is None else project_id,
        display_name=display_name,
        created_at=datetime.now(UTC),
    )


def load_project_identity(path: Path) -> ProjectIdentityLoadResult:
    """Load a JSON project identity or return a classified persistence result."""
    if not path.exists():
        message = f"Project identity file not found at '{path}' (PROJECT_IDENTITY_NOT_FOUND)."
        return ProjectIdentityLoadResult(
            status=ProjectIdentityLoadStatus.NOT_FOUND,
            path=path,
            error=ProjectIdentityError(
                error_type=ProjectIdentityErrorType.NOT_FOUND,
                message=message,
                path=str(path),
            ),
            errors=[message],
        )

    try:
        identity = ProjectIdentity.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        message = f"Project identity at '{path}' is invalid: {exc} (PROJECT_IDENTITY_INVALID_SCHEMA)."
        return ProjectIdentityLoadResult(
            status=ProjectIdentityLoadStatus.INVALID_SCHEMA,
            path=path,
            error=ProjectIdentityError(
                error_type=ProjectIdentityErrorType.INVALID_SCHEMA,
                message=message,
                path=str(path),
            ),
            errors=[message],
        )
    except (OSError, UnicodeDecodeError) as exc:
        message = f"Unable to read project identity at '{path}': {exc} (PROJECT_IDENTITY_UNREADABLE)."
        return ProjectIdentityLoadResult(
            status=ProjectIdentityLoadStatus.UNREADABLE,
            path=path,
            error=ProjectIdentityError(
                error_type=ProjectIdentityErrorType.UNREADABLE,
                message=message,
                path=str(path),
            ),
            errors=[message],
        )

    return ProjectIdentityLoadResult(
        status=ProjectIdentityLoadStatus.OK,
        path=path,
        identity=identity,
    )


def save_project_identity(path: Path, identity: ProjectIdentity) -> ProjectIdentitySaveResult:
    """Persist a project identity atomically or return a classified write result."""
    try:
        with WorkspaceLock(resolve_lock_file_path(path.parent)):
            Filesystem.atomic_write_json(path, identity.model_dump(mode="json"))
    except LockTimeoutError as exc:
        message = f"Unable to acquire project identity lock at '{path}': {exc} (PROJECT_IDENTITY_LOCKED)."
        return ProjectIdentitySaveResult(
            status=ProjectIdentitySaveStatus.LOCKED,
            path=path,
            error=ProjectIdentityError(
                error_type=ProjectIdentityErrorType.LOCKED,
                message=message,
                path=str(path),
            ),
            errors=[message],
        )
    except OSError as exc:
        message = f"Unable to write project identity at '{path}': {exc} (PROJECT_IDENTITY_WRITE_FAILED)."
        return ProjectIdentitySaveResult(
            status=ProjectIdentitySaveStatus.WRITE_FAILED,
            path=path,
            error=ProjectIdentityError(
                error_type=ProjectIdentityErrorType.WRITE_FAILED,
                message=message,
                path=str(path),
            ),
            errors=[message],
        )

    return ProjectIdentitySaveResult(status=ProjectIdentitySaveStatus.OK, path=path)


def _validate_project_id_format(project_id: str) -> str | None:
    """Return an error message when project_id does not match PROJECT_ID_REGEX, else None."""
    if re.match(PROJECT_ID_REGEX, project_id) is None:
        return f"Invalid project ID: must match {PROJECT_ID_REGEX}"

    return None


def _create_identity(
    path: Path,
    project_id: str | None,
    display_name: str | None,
    status: ProjectIdentityProvisionStatus,
) -> ProjectIdentityProvisionResult:
    """Generate and persist a fresh identity, returning the given status on success."""
    identity = generate_project_identity(project_id, display_name)
    save_result = save_project_identity(path, identity)
    if not save_result.ok:
        return ProjectIdentityProvisionResult(
            status=ProjectIdentityProvisionStatus.FAILED,
            path=path,
            errors=list(save_result.errors),
        )

    return ProjectIdentityProvisionResult(status=status, path=path, identity=identity)


def _preserve_existing_identity(
    path: Path,
    *,
    project_id: str | None,
    force: bool,
) -> ProjectIdentityProvisionResult:
    """Load and preserve the existing identity, warning the user why nothing changed."""
    loaded = load_project_identity(path)
    if not loaded.ok or loaded.identity is None:
        return ProjectIdentityProvisionResult(
            status=ProjectIdentityProvisionStatus.FAILED,
            path=path,
            errors=list(loaded.errors),
        )

    existing = loaded.identity
    if project_id is not None:
        warning = (
            f"Project identity already exists (id={existing.id}); ignoring --id '{project_id}' since --force "
            f"was not passed. Rerun with --id {project_id} --force to replace it."
        )
    elif force:
        warning = (
            f"Project identity already exists (id={existing.id}); --force has no effect without --id, "
            "so the existing identity was preserved."
        )
    else:
        warning = (
            f"Project identity already exists (id={existing.id}); preserving existing identity. "
            "Rerun with --id <new-id> --force to replace it."
        )

    return ProjectIdentityProvisionResult(
        status=ProjectIdentityProvisionStatus.PRESERVED,
        path=path,
        identity=existing,
        warnings=[warning],
    )


def provision_project_identity(
    dovo_dir: Path,
    *,
    project_id: str | None = None,
    display_name: str | None = None,
    force: bool = False,
) -> ProjectIdentityProvisionResult:
    """Create, preserve, or overwrite `<dovo_dir>/project.json` for `dovo init`."""
    path = dovo_dir / "project.json"

    if project_id is not None:
        format_error = _validate_project_id_format(project_id)
        if format_error is not None:
            return ProjectIdentityProvisionResult(
                status=ProjectIdentityProvisionStatus.INVALID_ID,
                path=path,
                errors=[format_error],
                fixes=[f"Pass a valid --id matching {PROJECT_ID_REGEX}, or omit --id to generate one."],
            )

    if not path.exists():
        return _create_identity(path, project_id, display_name, ProjectIdentityProvisionStatus.CREATED)

    if force and project_id is not None:
        return _create_identity(path, project_id, display_name, ProjectIdentityProvisionStatus.OVERWRITTEN)

    return _preserve_existing_identity(path, project_id=project_id, force=force)
