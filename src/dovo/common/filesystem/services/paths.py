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


def get_catalog_templates_dir() -> Traversable:
    """Return the packaged catalog templates resource root."""
    return importlib.resources.files("dovo.core.catalog.templates")
