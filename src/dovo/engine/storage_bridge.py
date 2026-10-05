"""Worktree storage bridge: the .dovo/run symlink from a run's worktree to its session directory."""

from __future__ import annotations

import platform
from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.engine.writer import get_session_dir


def create_storage_bridge(
    paths: WorkspacePaths, session_id: str, worktree_path: Path, warnings: list[str]
) -> str | None:
    """Link <worktree_path>/.dovo/run to paths.session_dir(session_id), returning an error message or None.

    Never deletes or modifies the session directory or non-symlink content at the link path. A Windows symlink
    failure with error code 50 or 1314 is appended to warnings instead of returned.
    """
    bridge_path = worktree_path / ".dovo" / "run"

    prepare_error = _prepare_bridge_directories(paths, session_id, bridge_path)
    if prepare_error is not None:
        return prepare_error

    clear_error = _clear_existing_bridge(bridge_path)
    if clear_error is not None:
        return clear_error

    return _create_bridge_symlink(bridge_path, paths.session_dir(session_id), warnings)


def remove_storage_bridge(worktree_path: Path) -> str | None:
    """Unlink <worktree_path>/.dovo/run without traversing its target, returning a failure message or None."""
    bridge_path = worktree_path / ".dovo" / "run"
    if not bridge_path.is_symlink():
        return None

    try:
        bridge_path.unlink()
    except OSError as exc:
        return f"Failed to unlink worktree storage bridge at '{bridge_path}': {exc}"

    return None


def _prepare_bridge_directories(paths: WorkspacePaths, session_id: str, bridge_path: Path) -> str | None:
    """Create the session directory and the worktree's .dovo directory."""
    try:
        get_session_dir(paths, session_id)
        bridge_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Unable to prepare worktree storage bridge at '{bridge_path}': {exc}"

    return None


def _clear_existing_bridge(bridge_path: Path) -> str | None:
    """Unlink an existing symlink at bridge_path, or reject a non-symlink path."""
    if bridge_path.is_symlink():
        try:
            bridge_path.unlink()
        except OSError as exc:
            return f"Unable to replace worktree storage bridge at '{bridge_path}': {exc}"
        return None

    if bridge_path.exists():
        return f"Worktree storage bridge path '{bridge_path}' is not a symlink."

    return None


def _create_bridge_symlink(bridge_path: Path, session_dir: Path, warnings: list[str]) -> str | None:
    """Create the symlink, downgrading documented Windows limitations to a warning."""
    try:
        bridge_path.symlink_to(session_dir, target_is_directory=True)
    except OSError as exc:
        message = f"Unable to create worktree storage bridge at '{bridge_path}': {exc}"
        if not _is_windows_symlink_fallback(exc):
            return message
        warnings.append(message)

    return None


def _is_windows_symlink_fallback(exc: OSError) -> bool:
    """Return whether an error is a documented Windows symlink limitation."""
    winerror = getattr(exc, "winerror", None)
    error_code = winerror if isinstance(winerror, int) else exc.errno
    return platform.system() == "Windows" and error_code in (50, 1314)
