from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable
from pathlib import Path

from dovo.common.filesystem.services.global_root import resolve_global_paths


def find_dovo_root(start: Path | None = None) -> Path:
    """Find the root directory of a Dovo workspace or git repository.

    Traverses upward from start (defaulting to CWD), skipping the directory
    that holds the global Dovo home (the parent of the resolved DOVO_HOME,
    default ~/.dovo) and any directory whose .dovo resolves to that home:
    - Returns the nearest other ancestor containing .dovo/ (or
      .dovo/config.json) or .git; .dovo wins within one directory.
    - If no other ancestor matches, returns resolved start.
    Reads markers only; never creates the global home or any directory.
    """
    current = (start or Path.cwd()).expanduser().resolve()
    global_root = resolve_global_paths().root
    for target in (current, *current.parents):
        if _is_global_home_parent(target, global_root):
            continue
        if _has_workspace_marker(target):
            return target

    return current


def _is_global_home_parent(directory: Path, global_root: Path) -> bool:
    """Return True when directory holds the resolved global Dovo home and must not be a workspace root."""
    return directory == global_root.parent or (directory / ".dovo").resolve() == global_root


def _has_workspace_marker(directory: Path) -> bool:
    """Return True when directory contains .dovo/config.json, a .dovo directory, or .git."""
    dovo_dir = directory / ".dovo"
    return (dovo_dir / "config.json").is_file() or dovo_dir.is_dir() or (directory / ".git").exists()


def get_catalog_templates_dir() -> Traversable:
    """Return the packaged catalog templates resource root."""
    return importlib.resources.files("dovo.core.catalog.templates")
