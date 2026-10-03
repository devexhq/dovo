"""Filesystem bootstrap service for the local Dovo directory (.dovo/).

Symlink policy: the .dovo root may be a symlink to a directory; required
subdirectories must be real directories (not symlinks).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dovo.common.constants import (
    BOOTSTRAP_META_REL,
    BOOTSTRAP_SCHEMA_VERSION,
    DOVO_GITIGNORE_CONTENT,
    REQUIRED_SUBDIRS,
)
from dovo.common.filesystem import Filesystem
from dovo.common.utils import display_path
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapResult,
    DirEnsureOutcome,
)
from dovo.core.catalog.models import SeedResult


def _validate_existing_dir(path: Path, *, allow_symlink: bool) -> None:
    """Raise ValueError when an existing path is not a usable directory."""
    if path.is_symlink():
        if allow_symlink:
            if not path.is_dir():
                raise ValueError(f"{display_path(path)} is a symlink that does not resolve to a directory.")
            return
        raise ValueError(f"{display_path(path)} must be a directory, not a symlink.")
    if path.is_file():
        raise ValueError(f"{display_path(path)} exists but is a file, not a directory.")
    if not path.is_dir():
        raise ValueError(f"{display_path(path)} is not a usable directory.")


def ensure_dir(path: Path, *, allow_symlink: bool = False) -> DirEnsureOutcome:
    """Create a directory if missing.

    Raises ValueError with an actionable message when the path is invalid.
    """
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(
                f"Could not create {display_path(path)}: {exc}. Check directory permissions and try again."
            ) from exc
        return DirEnsureOutcome.CREATED

    _validate_existing_dir(path, allow_symlink=allow_symlink)
    return DirEnsureOutcome.EXISTING


def assert_writable(path: Path) -> None:
    """Verify that an existing path is writable."""
    if not path.exists():
        raise ValueError(f"{display_path(path)} does not exist.")
    if not os.access(path, os.W_OK):
        raise ValueError(f"{display_path(path)} is not writable. Check directory permissions and try again.")


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


def _ensure_required_subdirs(root_path: Path, result: BootstrapResult) -> bool:
    """Ensure REQUIRED_SUBDIRS under root_path. Returns False when a path error is recorded."""
    for name in REQUIRED_SUBDIRS:
        sub_path = root_path / name
        try:
            outcome = ensure_dir(sub_path, allow_symlink=False)
        except ValueError as exc:
            result.errors.append(str(exc))
            return False
        if outcome == DirEnsureOutcome.CREATED:
            result.dirs_created.append(sub_path)
        else:
            result.dirs_existing.append(sub_path)
    return True


def _seed_local_gitignore(root_path: Path) -> bool:
    """Write `.dovo/.gitignore` with DOVO_GITIGNORE_CONTENT if it does not already exist."""
    gitignore_path = root_path / ".gitignore"
    if gitignore_path.exists():
        return False

    Filesystem.atomic_write_text(gitignore_path, DOVO_GITIGNORE_CONTENT)
    return True


def _assert_layout_writable(root_path: Path, result: BootstrapResult) -> bool:
    """Assert root and known subdirs are writable. Returns False when a path error is recorded."""
    try:
        assert_writable(root_path)
        for sub_path in result.dirs_created + result.dirs_existing:
            assert_writable(sub_path)
    except ValueError as exc:
        result.errors.append(str(exc))
        return False
    return True


def _classify_bootstrap_outcome(
    *,
    ok: bool,
    repaired: bool,
    root_created: bool,
    dirs_created: list[Path],
) -> BootstrapOutcome:
    """Classify bootstrap execution into canonical BootstrapOutcome enum."""
    if not ok:
        return BootstrapOutcome.FAILED
    if repaired:
        return BootstrapOutcome.REPAIRED
    if root_created or bool(dirs_created):
        return BootstrapOutcome.INITIALIZED
    return BootstrapOutcome.ALREADY_INITIALIZED


def _bootstrap_status(
    *,
    repaired: bool,
    root_created: bool,
    dirs_created: list[Path],
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
    dirs_created: list[Path],
    dirs_existing: list[Path],
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
) -> BootstrapResult:
    """Create and validate the Dovo home layout under root_path (typically .dovo).

    Idempotent: safe to run multiple times; never deletes user data.
    """
    root_path = root_path.resolve()
    result = BootstrapResult(root_path=root_path)
    meta_path = root_path / BOOTSTRAP_META_REL
    prior_meta = load_existing_bootstrap_metadata(meta_path)

    try:
        root_outcome = _ensure_dovo_root(root_path)
        result.root_created = root_outcome == DirEnsureOutcome.CREATED
    except ValueError as exc:
        result.errors.append(str(exc))
        result.outcome = BootstrapOutcome.FAILED
        return result

    if not result.root_created:
        try:
            assert_writable(root_path)
        except ValueError as exc:
            result.errors.append(str(exc))
            result.outcome = BootstrapOutcome.FAILED
            return result

    if not _ensure_required_subdirs(root_path, result):
        result.outcome = BootstrapOutcome.FAILED
        return result
    result.gitignore_created = _seed_local_gitignore(root_path)
    if not _assert_layout_writable(root_path, result):
        result.outcome = BootstrapOutcome.FAILED
        return result

    result.repaired = _is_repair(
        root_created=result.root_created,
        dirs_created=result.dirs_created,
        dirs_existing=result.dirs_existing,
        prior_meta=prior_meta,
    )
    if result.errors:
        result.outcome = BootstrapOutcome.FAILED
        return result

    result.outcome = _classify_bootstrap_outcome(
        ok=result.ok,
        repaired=result.repaired,
        root_created=result.root_created,
        dirs_created=result.dirs_created,
    )

    status = _bootstrap_status(
        repaired=result.repaired,
        root_created=result.root_created,
        dirs_created=result.dirs_created,
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
        result.errors.append(f"Could not write bootstrap metadata at {display_path(meta_path)}: {exc}")
        result.outcome = BootstrapOutcome.FAILED
        return result

    result.seed_result = SeedResult()
    return result
