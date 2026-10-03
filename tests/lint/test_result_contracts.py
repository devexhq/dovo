"""Tier 4 invariant: every *Result DTO inherits from BaseResult."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

from dovo.common.models import BaseResult
from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files


def _has_result_class_def(tree: ast.AST) -> bool:
    """Check if AST tree contains candidate Result class definitions."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Result") and node.name != "BaseResult":
            return True
    return False


def _file_to_module_name(file_path: Path) -> str:
    """Convert a file path under src/ to a dotted python module name."""
    rel = file_path.relative_to(REPO_ROOT / "src")
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_candidate_result_class(cls: type, cls_name: str, module_name: str) -> bool:
    """Check if class is a Result model defined in target module."""
    if getattr(cls, "__module__", None) != module_name:
        return False
    if cls_name == "BaseResult":
        return False
    return cls_name.endswith("Result")


def _inspect_module_result_classes(file_path: Path) -> list[str]:
    """Inspect classes ending with Result in a module to ensure BaseResult inheritance.

    Args:
        file_path: Path to the Python file in src/dovo.

    Returns:
        List of violation strings naming offending classes.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    if not _has_result_class_def(tree):
        return []

    module_name = _file_to_module_name(file_path)
    try:
        mod = importlib.import_module(module_name)
    except Exception as exc:
        return [f"Could not import {module_name}: {exc}"]

    violations: list[str] = []
    for cls_name, cls in inspect.getmembers(mod, inspect.isclass):
        if _is_candidate_result_class(cls, cls_name, module_name) and not issubclass(cls, BaseResult):
            violations.append(f"{module_name}.{cls_name} does not inherit from BaseResult")

    return violations


class ResultContractsTests:
    """Tier 4 result-hierarchy invariant tests."""

    def test_all_result_dtos_inherit_from_base_result(self) -> None:
        """Ensure all *Result DTO classes across src/dovo inherit from BaseResult."""
        files = collect_python_files(SRC_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_inspect_module_result_classes(file_path))

        assert not violations, "Found Result classes not inheriting from BaseResult:\n" + "\n".join(violations)
