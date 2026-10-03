"""Working-in-progress (WIP) file overlay services for worktrees."""

from __future__ import annotations

import shutil
from pathlib import Path

from dovo.core.git.runner import GitRunner
from dovo.core.worktree.exceptions import WorktreeError


def normalize_repo_rel(path: str) -> str:
    """Normalize a repository-relative path to forward slashes with whitespace stripped."""
    return path.strip().replace("\\", "/")


def list_wip_paths(path: Path) -> list[str]:
    """Return sorted repository-relative paths with uncommitted changes.

    Includes tracked modifications/deletions and untracked non-ignored files.
    """
    raw_lines = GitRunner.status_porcelain(path)
    paths: set[str] = set()
    for raw in raw_lines:
        if len(raw) < 4:
            continue
        entry = raw[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        rel = normalize_repo_rel(entry)
        if rel:
            paths.add(rel)
    return sorted(paths)


def remove_destination(dst: Path) -> None:
    """Remove destination regardless of whether it is a file, symlink, or directory."""
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()


def copy_wip_file(source_root: Path, dest_root: Path, rel: str) -> None:
    """Mirror a single working-tree path from source_root into dest_root.

    Behaviour by case:
    - Source deleted: remove the corresponding destination path.
    - Source is a plain directory: skip (implicitly created when children copied).
    - Source is a symlink: recreate the symlink at destination.
    - Source is a regular file: copy file preserving metadata.
    """
    src = source_root / rel
    dst = dest_root / rel
    if not src.exists():
        remove_destination(dst)
        return
    if src.is_dir() and not src.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        remove_destination(dst)
        dst.symlink_to(src.readlink())
        return
    shutil.copy2(src, dst)


def apply_wip_to_worktree(
    *,
    source_root: Path,
    worktree_path: Path,
) -> list[str]:
    """Overlay uncommitted working-tree changes into an existing worktree.

    Copies tracked and untracked (non-ignored) paths from source_root into
    worktree_path. Deleted tracked files are removed in the worktree.

    Args:
        source_root: Primary repository checkout (WIP source).
        worktree_path: worktree path.

    Returns:
        Sorted list of repo-relative paths touched by the overlay.

    Raises:
        WorktreeError: When overlay fails.
        GitPlumbingTimeoutError: When git status times out.
    """
    root = source_root.expanduser().resolve()
    dest = worktree_path.expanduser().resolve()
    if not dest.is_dir():
        raise WorktreeError(f"worktree path does not exist: {dest}")

    paths = list_wip_paths(root)
    try:
        for rel in paths:
            copy_wip_file(root, dest, rel)
    except OSError as exc:
        raise WorktreeError(str(exc)) from exc
    return paths
