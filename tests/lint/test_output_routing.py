"""Tier 4 invariant: no direct terminal output outside the CLI dispatcher."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

DISPATCHER_PATH: Final[Path] = SRC_ROOT / "cli" / "ui" / "dispatcher.py"


def _is_banned_echo_attribute(func: ast.AST) -> bool:
    """Check if AST node is typer.echo, typer.secho, click.echo, or click.secho."""
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in ("echo", "secho"):
        return False
    if not isinstance(func.value, ast.Name):
        return False
    return func.value.id in ("typer", "click")


def _check_call_for_banned_output(node: ast.Call, rel_path: Path) -> str | None:
    """Inspect an ast.Call node for banned direct output functions.

    Args:
        node: AST Call node.
        rel_path: File path relative to repo root.

    Returns:
        Violation string if banned call detected, None otherwise.
    """
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        return f"{rel_path}:{node.lineno}: Banned direct terminal output call 'print'"

    if _is_banned_echo_attribute(node.func):
        attr_node = node.func
        assert isinstance(attr_node, ast.Attribute)
        assert isinstance(attr_node.value, ast.Name)
        return f"{rel_path}:{node.lineno}: Banned direct terminal output call '{attr_node.value.id}.{attr_node.attr}'"

    return None


def _check_import_from_output(node: ast.ImportFrom, rel_path: Path) -> list[str]:
    """Check if ImportFrom node imports echo/secho from typer or click."""
    if node.module not in ("typer", "click"):
        return []
    violations: list[str] = []
    for alias in node.names:
        if alias.name in ("echo", "secho"):
            violations.append(
                f"{rel_path}:{node.lineno}: Banned direct terminal output import '{alias.name}' from '{node.module}'"
            )
    return violations


def _scan_file_for_terminal_output(file_path: Path) -> list[str]:
    """Scan a Python file for banned direct console output.

    Args:
        file_path: Path to the Python file.

    Returns:
        List of violation strings with file path and line number.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = file_path.relative_to(REPO_ROOT)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            violations.extend(_check_import_from_output(node, rel_path))
        elif isinstance(node, ast.Call):
            violation = _check_call_for_banned_output(node, rel_path)
            if violation:
                violations.append(violation)

    return violations


class OutputRoutingTests:
    """Tier 4 output-routing invariant tests."""

    def test_zero_direct_terminal_output_outside_dispatcher(self) -> None:
        all_files = collect_python_files(SRC_ROOT)
        files = [p for p in all_files if p.resolve() != DISPATCHER_PATH.resolve()]
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_terminal_output(file_path))

        assert not violations, "Found direct terminal output calls outside dispatcher.py:\n" + "\n".join(violations)
