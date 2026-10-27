"""Project identity creation and persistence services."""

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.filesystem import Filesystem
from dovo.common.lock import LockTimeoutError, WorkspaceLock, resolve_lock_file_path
from dovo.common.models import Remediation
from dovo.core.project.exceptions import ProjectIdentityError
from dovo.core.project.models import (
    PROJECT_ID_REGEX,
    ProjectIdentity,
    ProjectIdentityOutcome,
    ProvisionedProjectIdentity,
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


def validate_project_id(project_id: str) -> None:
    """Raise PROJECT_ID_INVALID when project_id does not match PROJECT_ID_REGEX."""
    if re.fullmatch(PROJECT_ID_REGEX, project_id) is None:
        raise ProjectIdentityError(
            ErrorCode.PROJECT_ID_INVALID,
            f"Invalid project ID: must match {PROJECT_ID_REGEX}",
            [
                Remediation(
                    message=f"Pass a valid '--id' matching '{PROJECT_ID_REGEX}', or omit '--id' to generate one."
                )
            ],
        )


def load_project_identity(path: Path) -> ProjectIdentity:
    """Load the JSON project identity at path, raising ProjectIdentityError when it is missing, invalid or unreadable."""
    if not path.exists():
        raise ProjectIdentityError(
            ErrorCode.PROJECT_IDENTITY_NOT_FOUND,
            f"Project identity file not found at '{path}'.",
        )

    try:
        return ProjectIdentity.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ProjectIdentityError(
            ErrorCode.PROJECT_IDENTITY_SCHEMA_INVALID,
            _sentence(f"Project identity at '{path}' is invalid: {exc}"),
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectIdentityError(
            ErrorCode.PROJECT_IDENTITY_UNREADABLE,
            _sentence(f"Unable to read project identity at '{path}': {exc}"),
        ) from exc


def save_project_identity(path: Path, identity: ProjectIdentity) -> None:
    """Write identity to path atomically under WorkspaceLock, raising ProjectIdentityError when locked or unwritable."""
    try:
        with WorkspaceLock(resolve_lock_file_path(path.parent)):
            Filesystem.atomic_write_json(path, identity.model_dump(mode="json"))
    except LockTimeoutError as exc:
        raise ProjectIdentityError(
            ErrorCode.PROJECT_IDENTITY_LOCKED,
            _sentence(f"Unable to acquire project identity lock at '{path}': {exc}"),
        ) from exc
    except OSError as exc:
        raise ProjectIdentityError(
            ErrorCode.PROJECT_IDENTITY_WRITE_FAILED,
            _sentence(f"Unable to write project identity at '{path}': {exc}"),
        ) from exc


def _sentence(text: str) -> str:
    """Return text ending in exactly one period."""
    return text if text.endswith(".") else f"{text}."


def _create_identity(
    path: Path,
    project_id: str | None,
    display_name: str | None,
    outcome: ProjectIdentityOutcome,
) -> ProvisionedProjectIdentity:
    """Generate and persist a fresh identity, reporting it with outcome."""
    identity = generate_project_identity(project_id, display_name)
    save_project_identity(path, identity)
    return ProvisionedProjectIdentity(identity, path, outcome)


def _preserve_existing_identity(path: Path, *, project_id: str | None, force: bool) -> ProvisionedProjectIdentity:
    """Load and preserve the existing identity, with the warning that says why nothing changed."""
    existing = load_project_identity(path)
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

    return ProvisionedProjectIdentity(existing, path, ProjectIdentityOutcome.PRESERVED, warning)


def provision_project_identity(
    dovo_dir: Path,
    *,
    project_id: str | None = None,
    display_name: str | None = None,
    force: bool = False,
) -> ProvisionedProjectIdentity:
    """Create, preserve, or overwrite `<dovo_dir>/project.json` for `dovo init`; load and save errors propagate."""
    path = dovo_dir / "project.json"

    if not path.exists():
        return _create_identity(path, project_id, display_name, ProjectIdentityOutcome.CREATED)

    if force and project_id is not None:
        return _create_identity(path, project_id, display_name, ProjectIdentityOutcome.OVERWRITTEN)

    return _preserve_existing_identity(path, project_id=project_id, force=force)
