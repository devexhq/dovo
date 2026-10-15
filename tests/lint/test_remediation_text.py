"""Tier 4 invariant: remediation fix strings begin with a capital letter."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.lint.astlib import (
    REPO_ROOT,
    SRC_ROOT,
    collect_python_files,
    extract_strings_from_node,
    extract_target_names,
)


def _check_fix_string(text: str, rel_path: Path, lineno: int) -> str | None:
    """Validate that a remediation fix string begins with a capital letter.

    Args:
        text: Remediation text string.
        rel_path: File path relative to repo root.
        lineno: Source code line number.

    Returns:
        Violation string if not capitalized, None otherwise.
    """
    trimmed = text.strip()
    if not trimmed:
        return f"{rel_path}:{lineno}: Remediation fix string is empty: {text!r}"

    stripped = trimmed.lstrip("\"'")
    if stripped.startswith("`"):
        return None

    if not stripped or not stripped[0].isupper():
        return f"{rel_path}:{lineno}: Remediation fix string does not begin with a capital letter: {text!r}"
    return None


def _validate_fix_expr(expr: ast.AST, rel_path: Path, violations: list[str]) -> None:
    """Validate all fix strings within an AST expression."""
    for text, lineno in extract_strings_from_node(expr):
        violation = _check_fix_string(text, rel_path, lineno)
        if violation:
            violations.append(violation)


def _is_fixes_append_call(node: ast.Call) -> bool:
    """Check if Call node is fixes.append/extend(...) or obj.fixes.append/extend(...)."""
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in ("append", "extend"):
        return False
    target = node.func.value
    if isinstance(target, ast.Name) and target.id == "fixes":
        return bool(node.args)
    if isinstance(target, ast.Attribute) and target.attr == "fixes":
        return bool(node.args)
    return False


def _check_call_fixes(node: ast.Call, rel_path: Path, violations: list[str]) -> None:
    """Check an ast.Call node for uncapitalized fixes arguments or append/extend calls."""
    for kw in node.keywords:
        if kw.arg == "fixes":
            _validate_fix_expr(kw.value, rel_path, violations)

    if _is_fixes_append_call(node):
        _validate_fix_expr(node.args[0], rel_path, violations)


def _is_remediation_variable(name: str) -> bool:
    """Check if variable name represents a remediation or fix container."""
    if "REMEDIATION" in name:
        return True
    if name.endswith("_FIXES") or name == "FIXES":
        return True
    return False


def _check_assign_fixes(node: ast.Assign | ast.AnnAssign, rel_path: Path, violations: list[str]) -> None:
    """Check assignment to remediation or fixes variables for uncapitalized strings."""
    if node.value is None:
        return
    target_names = extract_target_names(node)
    if not any(_is_remediation_variable(name) for name in target_names):
        return

    if isinstance(node.value, ast.Dict):
        for val in node.value.values:
            _validate_fix_expr(val, rel_path, violations)
    elif isinstance(node.value, (ast.List, ast.Tuple)):
        _validate_fix_expr(node.value, rel_path, violations)


def _scan_file_for_fix_strings(file_path: Path) -> list[str]:
    """Scan a Python file for remediation fix strings and validate capitalization.

    Args:
        file_path: Path to the Python file.

    Returns:
        List of violation strings naming uncapitalized remediation fixes.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = file_path.relative_to(REPO_ROOT)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _check_call_fixes(node, rel_path, violations)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            _check_assign_fixes(node, rel_path, violations)

    return violations


class RemediationTextTests:
    """Tier 4 remediation-capitalization invariant tests."""

    def test_all_emitted_remediation_fixes_are_capitalized(self) -> None:
        files = collect_python_files(SRC_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_fix_strings(file_path))

        assert not violations, "Found uncapitalized remediation fixes:\n" + "\n".join(violations)
