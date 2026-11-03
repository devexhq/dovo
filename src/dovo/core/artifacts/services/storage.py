"""Storage helpers shared by the artifact services."""

from __future__ import annotations

from pathlib import Path


def stored_cause(error: OSError, storage_dir: Path) -> OSError | None:
    """Return error when it hit a file under storage_dir (Dovo's own storage), else None for a worktree or destination failure."""
    filename = error.filename
    if isinstance(filename, str | Path) and Path(filename).is_relative_to(storage_dir):
        return error
    return None
