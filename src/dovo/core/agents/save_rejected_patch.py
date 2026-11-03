"""Save a rejected direct-mutation edit as a binary-safe patch before it is discarded."""

from __future__ import annotations

from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import Filesystem
from dovo.core.agents.exceptions import AgentError
from dovo.core.agents.mutation_git import capture_binary_patch_since
from dovo.core.git import GitRunner


def save_rejected_patch(worktree_path: Path, baseline: str, destination: Path, *, git: GitRunner) -> Path | None:
    """Write the worktree's full change set since ``baseline`` to ``destination`` and return it; return None, writing nothing, when the change set is empty.

    Raises:
        GitError: GIT_FAILED, GIT_TIMEOUT, GIT_BINARY_MISSING or GIT_WORKDIR_MISSING when a git command fails.
        AgentError: AGENT_PATCH_WRITE_FAILED when ``destination`` cannot be written.
    """
    patch = capture_binary_patch_since(worktree_path, baseline, git=git)
    if not patch:
        return None

    try:
        Filesystem.atomic_write_bytes(destination, patch)
    except OSError as exc:
        raise AgentError(
            ErrorCode.AGENT_PATCH_WRITE_FAILED, f"cannot write {destination}: {exc.strerror or exc}"
        ) from exc

    return destination
