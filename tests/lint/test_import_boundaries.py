"""Tier 4 invariant: layer isolation of worktree.core (no cli, no engine outside core/engine/) and tests.core (no cli)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from tests.lint.astlib import (
    REPO_ROOT,
    SRC_ROOT,
    TESTS_ROOT,
    check_import_from_node,
    check_import_node,
    collect_python_files,
)

CORE_ROOT: Final[Path] = SRC_ROOT / "core"
CORE_TESTS_ROOT: Final[Path] = TESTS_ROOT / "core"
ENGINE_ROOT: Final[Path] = CORE_ROOT / "engine"


def _core_files_outside_engine() -> list[Path]:
    """Collect core source files that are not inside core/engine/."""
    return [path for path in collect_python_files(CORE_ROOT) if ENGINE_ROOT not in path.parents]


def _scan_file_for_banned_imports(file_path: Path, banned_prefix: str) -> list[str]:
    """Scan a Python file for banned import statements using AST analysis.

    Args:
        file_path: Path to the Python file.
        banned_prefix: Banned package prefix (e.g. 'worktree.cli').

    Returns:
        List of informative violation strings with file path and line number.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = file_path.relative_to(REPO_ROOT)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(check_import_node(node, banned_prefix, rel_path))
        elif isinstance(node, ast.ImportFrom):
            violations.extend(check_import_from_node(node, banned_prefix, rel_path))

    return violations


class ImportBoundariesTests:
    """Tier 4 layer-isolation invariant tests."""

    def test_core_never_imports_worktree_cli(self) -> None:
        """Ensure src/worktree/core never imports from worktree.cli."""
        files = collect_python_files(CORE_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_banned_imports(file_path, "worktree.cli"))

        assert not violations, "Found prohibited worktree.cli imports in src/worktree/core:\n" + "\n".join(violations)

    def test_core_outside_engine_never_imports_worktree_core_engine(self) -> None:
        """Ensure no src/worktree/core module outside core/engine/ imports worktree.core.engine."""
        violations: list[str] = []
        for file_path in _core_files_outside_engine():
            violations.extend(_scan_file_for_banned_imports(file_path, "worktree.core.engine"))

        assert not violations, "Found prohibited worktree.core.engine imports outside core/engine:\n" + "\n".join(
            violations
        )

    def test_tests_core_never_imports_worktree_cli(self) -> None:
        """Ensure core tests never import from worktree.cli."""
        files = collect_python_files(CORE_TESTS_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_banned_imports(file_path, "worktree.cli"))

        assert not violations, "Found prohibited worktree.cli imports in tests/core:\n" + "\n".join(violations)
