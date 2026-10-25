"""Shared AST helpers for tests/lint invariant checks."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).parent.parent.parent
SRC_ROOT: Final[Path] = REPO_ROOT / "src" / "dovo"
TESTS_ROOT: Final[Path] = REPO_ROOT / "tests"
E2E_ROOT: Final[Path] = REPO_ROOT / "e2e"


def collect_python_files(directory: Path) -> list[Path]:
    """Collect all python source files within a directory tree.

    Args:
        directory: Target root path to search.

    Returns:
        List of python file paths. Returns empty list if directory does not exist.
    """
    if not directory.exists() or not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.py") if p.is_file())


def _is_banned_import(name: str | None, banned_prefix: str) -> bool:
    """Check if import name matches banned prefix."""
    if not name:
        return False
    if name == banned_prefix:
        return True
    return name.startswith(f"{banned_prefix}.")


def check_import_node(node: ast.Import, banned_prefix: str, rel_path: Path) -> list[str]:
    """Check an Import node for banned prefixes."""
    violations: list[str] = []
    for alias in node.names:
        if _is_banned_import(alias.name, banned_prefix):
            violations.append(f"{rel_path}:{node.lineno}: Layer violation: imports '{alias.name}'")
    return violations


def check_import_from_node(node: ast.ImportFrom, banned_prefix: str, rel_path: Path) -> list[str]:
    """Check an ImportFrom node for banned prefixes."""
    if _is_banned_import(node.module, banned_prefix):
        return [f"{rel_path}:{node.lineno}: Layer violation: imports '{node.module}'"]
    return []
