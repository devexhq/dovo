"""Tier 4 invariant: layer isolation of worktree.core (no cli, no engine outside core/engine/), catalog/inputs layer direction, and tests.core (no cli)."""

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
CATALOG_ROOT: Final[Path] = CORE_ROOT / "catalog"
INPUTS_ROOT: Final[Path] = CORE_ROOT / "inputs"
CATALOG_BANNED_PREFIXES: Final[tuple[str, ...]] = (
    "worktree.core.agents",
    "worktree.core.step",
    "worktree.core.engine",
    "worktree.core.blueprint",
    "worktree.core.logs",
    "worktree.core.history",
    "worktree.cli",
)


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


def _violations_under(root: Path, banned_prefix: str) -> list[str]:
    """Collect banned-import violations for every Python file under root."""
    violations: list[str] = []
    for file_path in collect_python_files(root):
        violations.extend(_scan_file_for_banned_imports(file_path, banned_prefix))
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

    def test_catalog_never_imports_higher_layers(self) -> None:
        """[tier-4/unit] src/worktree/core/catalog: no module imports worktree.core.agents, .step, .engine, .blueprint, .logs, .history, or worktree.cli; violations list is empty."""
        assert collect_python_files(CATALOG_ROOT)

        violations: list[str] = []
        for banned_prefix in CATALOG_BANNED_PREFIXES:
            violations.extend(_violations_under(CATALOG_ROOT, banned_prefix))

        assert not violations, "Found prohibited higher-layer imports in src/worktree/core/catalog:\n" + "\n".join(
            violations
        )

    def test_inputs_never_imports_catalog(self) -> None:
        """[tier-4/unit] src/worktree/core/inputs: no module imports worktree.core.catalog; violations list is empty."""
        assert collect_python_files(INPUTS_ROOT)

        violations = _violations_under(INPUTS_ROOT, "worktree.core.catalog")

        assert not violations, (
            "Found prohibited worktree.core.catalog imports in src/worktree/core/inputs:\n" + "\n".join(violations)
        )

    def test_banned_prefix_matches_only_on_dotted_boundary(self) -> None:
        """[tier-4/unit] check_import_from_node: "from worktree.core.step import X" is flagged for prefix "worktree.core.step", while "from worktree.core.catalog.blueprint import Blueprint" yields no violation for prefix "worktree.core.blueprint"."""
        banned = ast.parse("from worktree.core.step import X").body[0]
        sibling = ast.parse("from worktree.core.catalog.blueprint import Blueprint").body[0]
        assert isinstance(banned, ast.ImportFrom)
        assert isinstance(sibling, ast.ImportFrom)

        assert check_import_from_node(banned, "worktree.core.step", Path("x.py"))
        assert not check_import_from_node(sibling, "worktree.core.blueprint", Path("x.py"))
