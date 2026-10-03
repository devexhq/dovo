"""Tier 4 invariant: every module- and class-level function and method in src/dovo must carry a docstring."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files


def _collect_target_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Collect non-inner functions and methods (module-level and class-level).

    Inner/nested functions defined inside other function bodies are excluded.

    Args:
        tree: AST root of the module.

    Returns:
        List of target function and method nodes.
    """
    target_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def _traverse(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                target_nodes.append(child)
                # Do not recurse into function bodies; inner functions are exempt.
            elif isinstance(child, (ast.ClassDef, ast.Module, ast.If, ast.Try, ast.With, ast.AsyncWith)):
                _traverse(child)

    _traverse(tree)
    return target_nodes


def _is_overload_or_accessor(decorator: ast.expr) -> bool:
    """Check if decorator is @overload, @*.setter, or @*.deleter."""
    if isinstance(decorator, ast.Name):
        return decorator.id == "overload"
    if isinstance(decorator, ast.Attribute):
        return decorator.attr in ("overload", "setter", "deleter")
    return False


def _is_exempt_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check whether a function or method node is exempt from docstring requirements.

    Args:
        node: Function or async function AST node.

    Returns:
        True if the function is exempt (dunders, overloads, or property setters/deleters).
    """
    if node.name.startswith("__") and node.name.endswith("__"):
        return True
    return any(_is_overload_or_accessor(d) for d in node.decorator_list)


def _check_file_docstrings(file_path: Path) -> list[str]:
    """Scan a Python file for functions and methods missing docstrings.

    Args:
        file_path: Path to the Python file.

    Returns:
        List of formatted violation strings.
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except Exception as exc:
        return [f"{file_path}: Failed to parse AST: {exc}"]

    rel_path = file_path.relative_to(REPO_ROOT)
    violations: list[str] = []

    for node in _collect_target_functions(tree):
        if _is_exempt_function(node):
            continue
        doc = ast.get_docstring(node)
        if not doc or not doc.strip():
            violations.append(f"{rel_path}:{node.lineno}: Function/method '{node.name}' is missing a docstring")

    return violations


class DocstringsCoverageTests:
    """Tier 4 invariant: docstrings coverage across all production functions and methods."""

    def test_all_production_functions_and_methods_have_docstrings(self) -> None:
        """[tier-4/unit] Ensure every function and method under src/dovo/ has a non-empty docstring."""
        files = collect_python_files(SRC_ROOT)
        violations: list[str] = []

        for file_path in files:
            violations.extend(_check_file_docstrings(file_path))

        assert not violations, (
            f"Found {len(violations)} function(s)/method(s) missing docstrings:\n"
            + "\n".join(violations[:50])
            + (f"\n... and {len(violations) - 50} more" if len(violations) > 50 else "")
        )
