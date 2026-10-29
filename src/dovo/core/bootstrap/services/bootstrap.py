"""Filesystem bootstrap service for the local Dovo directory (.dovo/).

Symlink policy: the .dovo root may be a symlink to a directory; required
subdirectories must be real directories (not symlinks).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from dovo.common.constants import (
    BOOTSTRAP_META_REL,
    BOOTSTRAP_SCHEMA_VERSION,
    DOVO_GITIGNORE_CONTENT,
    REQUIRED_SUBDIRS,
)
from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.common.models import Remediation
from dovo.common.utils import display_path
from dovo.core.bootstrap.exceptions import BootstrapError
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapSummary,
    DirEnsureOutcome,
)

_RERUN_REMEDIATION = Remediation(message="Resolve the path conflict above, then rerun 'dovo init'")


def _bootstrap_error(error_code: ErrorCode, message: str) -> BootstrapError:
    """Return a BootstrapError carrying the rerun remediation every bootstrap failure shares."""
    return BootstrapError(error_code, message, [_RERUN_REMEDIATION])


def _validate_existing_dir(path: Path, *, allow_symlink: bool) -> None:
    """Raise BootstrapError when an existing path is not a usable directory."""
    if path.is_symlink():
        if allow_symlink:
            if not path.is_dir():
                raise _bootstrap_error(
                    ErrorCode.BOOTSTRAP_PATH_INVALID,
                    f"{display_path(path)} is a symlink that does not resolve to a directory.",
                )
            return
        raise _bootstrap_error(
            ErrorCode.BOOTSTRAP_PATH_INVALID, f"{display_path(path)} must be a directory, not a symlink."
        )
    if path.is_file():
        raise _bootstrap_error(
            ErrorCode.BOOTSTRAP_PATH_INVALID, f"{display_path(path)} exists but is a file, not a directory."
        )
    if not path.is_dir():
        raise _bootstrap_error(ErrorCode.BOOTSTRAP_PATH_INVALID, f"{display_path(path)} is not a usable directory.")


def ensure_dir(path: Path, *, allow_symlink: bool = False) -> DirEnsureOutcome:
    """Create a directory if missing.

    Raises:
        BootstrapError: BOOTSTRAP_PATH_INVALID, with an actionable message, when the path cannot be created or is
            not a usable directory.
    """
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _bootstrap_error(
                ErrorCode.BOOTSTRAP_PATH_INVALID,
                f"Could not create {display_path(path)}: {exc}. Check directory permissions and try again.",
            ) from exc
        return DirEnsureOutcome.CREATED

    _validate_existing_dir(path, allow_symlink=allow_symlink)
    return DirEnsureOutcome.EXISTING


def assert_writable(path: Path) -> None:
    """Verify that an existing path is writable.

    Raises:
        BootstrapError: BOOTSTRAP_PATH_UNWRITABLE when the path is missing or not writable.
    """
    if not path.exists():
        raise _bootstrap_error(ErrorCode.BOOTSTRAP_PATH_UNWRITABLE, f"{display_path(path)} does not exist.")
    if not os.access(path, os.W_OK):
        raise _bootstrap_error(
            ErrorCode.BOOTSTRAP_PATH_UNWRITABLE,
            f"{display_path(path)} is not writable. Check directory permissions and try again.",
        )


def load_existing_bootstrap_metadata(meta_path: Path) -> dict[str, object] | None:
    """Load .meta/bootstrap.json if present and valid."""
    if not meta_path.is_file():
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_bootstrap_metadata(
    meta_path: Path,
    *,
    root_path: Path,
    status: str,
    tool_version: str | None,
    initialized_at: str | None,
) -> None:
    """Write bootstrap metadata for diagnostics and idempotent init."""
    now = datetime.now(UTC).isoformat()
    payload: dict[str, object] = {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "initialized_at": initialized_at or now,
        "last_checked_at": now,
        "tool_version": tool_version,
        "status": status,
        "root_path": str(root_path),
    }
    Filesystem.atomic_write_json(meta_path, payload)


def _ensure_required_subdirs(root_path: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Ensure REQUIRED_SUBDIRS under root_path; return the (created, existing) subdirectories."""
    created: list[Path] = []
    existing: list[Path] = []
    for name in REQUIRED_SUBDIRS:
        sub_path = root_path / name
        if ensure_dir(sub_path, allow_symlink=False) == DirEnsureOutcome.CREATED:
            created.append(sub_path)
        else:
            existing.append(sub_path)
    return tuple(created), tuple(existing)


def _seed_local_gitignore(root_path: Path) -> bool:
    """Write `.dovo/.gitignore` with DOVO_GITIGNORE_CONTENT, or append its missing entries to an existing file.

    Existing lines are kept as they are. Returns True only when the file was created.
    """
    gitignore_path = root_path / ".gitignore"
    if not gitignore_path.exists():
        Filesystem.atomic_write_text(gitignore_path, DOVO_GITIGNORE_CONTENT)
        return True

    existing = gitignore_path.read_text(encoding="utf-8")
    present = {line.strip() for line in existing.splitlines()}
    missing = [entry for entry in DOVO_GITIGNORE_CONTENT.splitlines() if entry not in present]
    if missing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        Filesystem.atomic_write_text(gitignore_path, existing + separator + "".join(f"{entry}\n" for entry in missing))
    return False


def _assert_layout_writable(root_path: Path, sub_paths: Iterable[Path]) -> None:
    """Assert root and known subdirs are writable; raise BootstrapError for the first that is not."""
    assert_writable(root_path)
    for sub_path in sub_paths:
        assert_writable(sub_path)


def _classify_bootstrap_outcome(
    *,
    repaired: bool,
    root_created: bool,
    dirs_created: tuple[Path, ...],
) -> BootstrapOutcome:
    """Classify bootstrap execution into canonical BootstrapOutcome enum."""
    if repaired:
        return BootstrapOutcome.REPAIRED
    if root_created or bool(dirs_created):
        return BootstrapOutcome.INITIALIZED
    return BootstrapOutcome.ALREADY_INITIALIZED


def _bootstrap_status(
    *,
    repaired: bool,
    root_created: bool,
    dirs_created: tuple[Path, ...],
    prior_meta: dict[str, object] | None,
) -> str:
    """Compute status string for metadata recording."""
    if repaired:
        return "repaired"
    if root_created or dirs_created:
        return "initialized"
    if prior_meta:
        return str(prior_meta.get("status", "initialized"))
    return "initialized"


def _is_repair(
    *,
    root_created: bool,
    dirs_created: tuple[Path, ...],
    dirs_existing: tuple[Path, ...],
    prior_meta: dict[str, object] | None,
) -> bool:
    """True when missing pieces were added to an already-present Dovo layout."""
    if not dirs_created:
        return False
    # Partial tree, re-init after prior bootstrap, or root already existed.
    return bool(dirs_existing) or prior_meta is not None or not root_created


def _ensure_dovo_root(root_path: Path) -> DirEnsureOutcome:
    """Ensure Dovo root directory exists, permitting symlinks."""
    return ensure_dir(root_path, allow_symlink=True)


def bootstrap_dovo(
    root_path: Path,
    *,
    tool_version: str | None = None,
) -> BootstrapSummary:
    """Create and validate the Dovo home layout under root_path (typically .dovo).

    Idempotent: safe to run multiple times; never deletes user data.

    Raises:
        BootstrapError: If a required directory is invalid or unwritable, or the bootstrap metadata write fails.
    """
    root_path = root_path.resolve()
    meta_path = root_path / BOOTSTRAP_META_REL
    prior_meta = load_existing_bootstrap_metadata(meta_path)

    root_created = _ensure_dovo_root(root_path) == DirEnsureOutcome.CREATED
    if not root_created:
        assert_writable(root_path)

    dirs_created, dirs_existing = _ensure_required_subdirs(root_path)
    gitignore_created = _seed_local_gitignore(root_path)
    _assert_layout_writable(root_path, (*dirs_created, *dirs_existing))

    repaired = _is_repair(
        root_created=root_created,
        dirs_created=dirs_created,
        dirs_existing=dirs_existing,
        prior_meta=prior_meta,
    )
    status = _bootstrap_status(
        repaired=repaired,
        root_created=root_created,
        dirs_created=dirs_created,
        prior_meta=prior_meta,
    )
    initialized_at = str(prior_meta["initialized_at"]) if prior_meta and prior_meta.get("initialized_at") else None

    try:
        write_bootstrap_metadata(
            meta_path,
            root_path=root_path,
            status=status,
            tool_version=tool_version,
            initialized_at=initialized_at,
        )
    except OSError as exc:
        raise _bootstrap_error(
            ErrorCode.BOOTSTRAP_METADATA_WRITE_FAILED,
            f"Could not write bootstrap metadata at {display_path(meta_path)}: {exc}",
        ) from exc

    return BootstrapSummary(
        root_path=root_path,
        outcome=_classify_bootstrap_outcome(repaired=repaired, root_created=root_created, dirs_created=dirs_created),
        root_created=root_created,
        dirs_created=dirs_created,
        dirs_existing=dirs_existing,
        repaired=repaired,
        gitignore_created=gitignore_created,
    )
