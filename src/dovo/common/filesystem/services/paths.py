from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable
from pathlib import Path


def find_dovo_root(start: Path | None = None) -> Path:
    """Find the root directory of a Dovo workspace or git repository.

    Traverses upward from start (defaulting to CWD):
    - Returns the nearest ancestor containing a .dovo directory or
      .dovo/config.json file.
    - Returns the nearest ancestor containing .git (directory or file).
    - If neither is found in any ancestor, returns resolved start.
    """
    current = (start or Path.cwd()).expanduser().resolve()
    target = current
    while True:
        if (target / ".dovo" / "config.json").is_file() or (target / ".dovo").is_dir():
            return target
        if (target / ".git").exists():
            return target
        if target.parent == target:
            break
        target = target.parent

    return current


def get_dovo_dir(cwd: Path) -> Path:
    """Return the Dovo root path, relative to the CWD."""
    return cwd / ".dovo"


def get_dovo_config_file(cwd: Path) -> Path:
    """Return the Dovo config file path, relative to CWD."""
    return get_dovo_dir(cwd) / "config.json"


def get_gitignore_file(cwd: Path) -> Path:
    """Return the .gitignore file path, relative to CWD."""
    return cwd / ".gitignore"


def get_catalog_templates_dir() -> Traversable:
    """Return the packaged catalog templates resource root."""
    return importlib.resources.files("dovo.core.catalog.templates")
