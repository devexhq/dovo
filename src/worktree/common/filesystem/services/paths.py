from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable
from pathlib import Path


def find_worktree_root(start: Path | None = None) -> Path:
    """Find the root directory of a worktree workspace or git repository.

    Traverses upward from start (defaulting to CWD):
    - Returns the nearest ancestor containing a .worktree directory or
      .worktree/config.json file.
    - Returns the nearest ancestor containing .git (directory or file).
    - If neither is found in any ancestor, returns resolved start.
    """
    current = (start or Path.cwd()).expanduser().resolve()
    target = current
    while True:
        if (target / ".worktree" / "config.json").is_file() or (target / ".worktree").is_dir():
            return target
        if (target / ".git").exists():
            return target
        if target.parent == target:
            break
        target = target.parent

    return current


def get_worktree_dir(cwd: Path) -> Path:
    """Return the worktree root path, relative to the CWD."""
    return cwd / ".worktree"


def get_worktree_config_file(cwd: Path) -> Path:
    """Return the worktree config file path, relative to CWD."""
    return get_worktree_dir(cwd) / "config.json"


def get_gitignore_file(cwd: Path) -> Path:
    """Return the .gitignore file path, relative to CWD."""
    return cwd / ".gitignore"


def get_catalog_templates_dir() -> Traversable:
    """Return the packaged catalog templates resource root."""
    return importlib.resources.files("worktree.core.catalog.templates")
