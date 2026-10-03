"""Shared AST helpers for tests/lint invariant checks."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).parent.parent.parent
SRC_ROOT: Final[Path] = REPO_ROOT / "src" / "dovo"
TESTS_ROOT: Final[Path] = REPO_ROOT / "tests"


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


def _extract_joined_str(expr: ast.JoinedStr) -> list[tuple[str, int]]:
    """Extract initial string literal prefix from an f-string."""
    if not expr.values:
        return []
    first_val = expr.values[0]
    if isinstance(first_val, ast.Constant) and isinstance(first_val.value, str):
        return [(first_val.value, expr.lineno)]
    return []


def extract_strings_from_node(expr: ast.AST) -> list[tuple[str, int]]:
    """Extract candidate remediation strings and their line numbers from an AST node."""
    if isinstance(expr, (ast.List, ast.Tuple)):
        strings: list[tuple[str, int]] = []
        for elt in expr.elts:
            strings.extend(extract_strings_from_node(elt))
        return strings

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [(expr.value, expr.lineno)]

    if isinstance(expr, ast.JoinedStr):
        return _extract_joined_str(expr)

    return []


def extract_target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Extract variable names from an assignment node."""
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []
