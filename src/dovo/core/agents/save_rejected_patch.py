"""Save a rejected direct-mutation edit as a binary-safe patch before it is discarded."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import Filesystem
from dovo.core.agents.mutation_git import MutationGitError, capture_binary_patch_since


def save_rejected_patch(worktree_path: Path, baseline: str, destination: Path) -> Path | None:
    """Write the worktree's full change set since ``baseline`` to ``destination`` and return it; return None, writing nothing, when the change set is empty.

    Raises:
        MutationGitError: When git fails or ``destination`` cannot be written.
    """
    patch = capture_binary_patch_since(worktree_path, baseline)
    if not patch:
        return None

    try:
        Filesystem.atomic_write_bytes(destination, patch)
    except OSError as exc:
        raise MutationGitError(f"cannot write {destination}: {exc.strerror or exc}") from exc

    return destination
