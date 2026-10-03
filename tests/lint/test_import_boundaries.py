"""Tier 4 invariant: layer isolation of dovo.core (no cli, no engine), catalog/artifacts layer direction, engine (no cli), inputs layer direction, and tests.core (no cli)."""

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
ENGINE_ROOT: Final[Path] = SRC_ROOT / "engine"
ARTIFACTS_ROOT: Final[Path] = CORE_ROOT / "artifacts"
CATALOG_ROOT: Final[Path] = CORE_ROOT / "catalog"
INPUTS_ROOT: Final[Path] = CORE_ROOT / "inputs"
CATALOG_BANNED_PREFIXES: Final[tuple[str, ...]] = (
    "dovo.core.agents",
    "dovo.core.logs",
    "dovo.core.history",
    "dovo.engine",
    "dovo.cli",
)


def _scan_file_for_banned_imports(file_path: Path, banned_prefix: str) -> list[str]:
    """Scan a Python file for banned import statements using AST analysis.

    Args:
        file_path: Path to the Python file.
        banned_prefix: Banned package prefix (e.g. 'dovo.cli').

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

    def test_core_never_imports_dovo_cli(self) -> None:
        """Ensure src/dovo/core never imports from dovo.cli."""
        files = collect_python_files(CORE_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_banned_imports(file_path, "dovo.cli"))

        assert not violations, "Found prohibited dovo.cli imports in src/dovo/core:\n" + "\n".join(violations)

    def test_core_never_imports_dovo_engine(self) -> None:
        """[tier-4/unit] src/dovo/core: no module imports dovo.engine; violations list is empty."""
        assert collect_python_files(CORE_ROOT)

        violations = _violations_under(CORE_ROOT, "dovo.engine")

        assert not violations, "Found prohibited dovo.engine imports in src/dovo/core:\n" + "\n".join(violations)

    def test_artifacts_never_imports_engine(self) -> None:
        """[tier-4/unit] src/dovo/core/artifacts: no module imports dovo.engine; violations list is empty."""
        assert collect_python_files(ARTIFACTS_ROOT)

        violations = _violations_under(ARTIFACTS_ROOT, "dovo.engine")

        assert not violations, "Found prohibited dovo.engine imports in src/dovo/core/artifacts:\n" + "\n".join(
            violations
        )

    def test_engine_never_imports_dovo_cli(self) -> None:
        """[tier-4/unit] src/dovo/engine: no module imports dovo.cli; violations list is empty."""
        assert collect_python_files(ENGINE_ROOT)

        violations = _violations_under(ENGINE_ROOT, "dovo.cli")

        assert not violations, "Found prohibited dovo.cli imports in src/dovo/engine:\n" + "\n".join(violations)

    def test_tests_core_never_imports_dovo_cli(self) -> None:
        """Ensure core tests never import from dovo.cli."""
        files = collect_python_files(CORE_TESTS_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_banned_imports(file_path, "dovo.cli"))

        assert not violations, "Found prohibited dovo.cli imports in tests/core:\n" + "\n".join(violations)

    def test_catalog_never_imports_higher_layers(self) -> None:
        """[tier-4/unit] src/dovo/core/catalog: no module imports dovo.core.agents, .logs, .history, dovo.engine, or dovo.cli; violations list is empty."""
        assert collect_python_files(CATALOG_ROOT)

        violations: list[str] = []
        for banned_prefix in CATALOG_BANNED_PREFIXES:
            violations.extend(_violations_under(CATALOG_ROOT, banned_prefix))

        assert not violations, "Found prohibited higher-layer imports in src/dovo/core/catalog:\n" + "\n".join(
            violations
        )

    def test_inputs_never_imports_catalog(self) -> None:
        """[tier-4/unit] src/dovo/core/inputs: no module imports dovo.core.catalog; violations list is empty."""
        assert collect_python_files(INPUTS_ROOT)

        violations = _violations_under(INPUTS_ROOT, "dovo.core.catalog")

        assert not violations, "Found prohibited dovo.core.catalog imports in src/dovo/core/inputs:\n" + "\n".join(
            violations
        )

    def test_banned_prefix_matches_only_on_dotted_boundary(self) -> None:
        """[tier-4/unit] check_import_from_node: "from dovo.engine import X" is flagged for prefix "dovo.engine", while "from dovo.engineering import Y" yields no violation for the same prefix."""
        banned = ast.parse("from dovo.engine import X").body[0]
        sibling = ast.parse("from dovo.engineering import Y").body[0]
        assert isinstance(banned, ast.ImportFrom)
        assert isinstance(sibling, ast.ImportFrom)

        assert check_import_from_node(banned, "dovo.engine", Path("x.py"))
        assert not check_import_from_node(sibling, "dovo.engine", Path("x.py"))
