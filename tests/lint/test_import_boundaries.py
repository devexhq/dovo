"""Tier 4 invariant: tests.core never imports cli, the sessions package layout stays flat, and retired core packages stay gone. Layer direction lives in test_layer_direction.py."""

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
SESSIONS_ROOT: Final[Path] = CORE_ROOT / "sessions"
SESSIONS_ALLOWED_ENTRIES: Final[frozenset[str]] = frozenset({"__init__.py", "models.py", "sessions.py", "services"})
SESSIONS_SERVICES_ALLOWED_ENTRIES: Final[frozenset[str]] = frozenset(
    {"__init__.py", "read_diff.py", "read_logs.py", "reconcile.py"}
)
RETIRED_CORE_PACKAGES: Final[tuple[str, ...]] = ("patch", "diff", "history", "logs", "doctor")


def _unexpected_entries(root: Path, allowed: frozenset[str]) -> list[str]:
    """Return the sorted names under root that are not in allowed, ignoring __pycache__."""
    return sorted(entry.name for entry in root.iterdir() if entry.name != "__pycache__" and entry.name not in allowed)


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


def _imported_modules(file_path: Path) -> list[tuple[str, int]]:
    """Return (dotted module, line) for every absolute import statement in file_path."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append((node.module, node.lineno))
    return imported


class ImportBoundariesTests:
    """Tier 4 layer-isolation invariant tests."""

    def test_tests_core_never_imports_dovo_cli(self) -> None:
        """Ensure core tests never import from dovo.cli."""
        files = collect_python_files(CORE_TESTS_ROOT)
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_banned_imports(file_path, "dovo.cli"))

        assert not violations, "Found prohibited dovo.cli imports in tests/core:\n" + "\n".join(violations)

    def test_banned_prefix_matches_only_on_dotted_boundary(self) -> None:
        """[tier-4/unit] check_import_from_node: "from dovo.engine import X" is flagged for prefix "dovo.engine", while "from dovo.engineering import Y" yields no violation for the same prefix."""
        banned = ast.parse("from dovo.engine import X").body[0]
        sibling = ast.parse("from dovo.engineering import Y").body[0]
        assert isinstance(banned, ast.ImportFrom)
        assert isinstance(sibling, ast.ImportFrom)

        assert check_import_from_node(banned, "dovo.engine", Path("x.py"))
        assert not check_import_from_node(sibling, "dovo.engine", Path("x.py"))

    def test_sessions_package_layout_is_flat(self) -> None:
        """[tier-4/unit] src/dovo/core/sessions: direct entries are exactly __init__.py, models.py, sessions.py, services; services/ holds exactly __init__.py, read_diff.py, read_logs.py, reconcile.py; unexpected-entry list is empty."""
        assert collect_python_files(SESSIONS_ROOT)

        unexpected = _unexpected_entries(SESSIONS_ROOT, SESSIONS_ALLOWED_ENTRIES)
        unexpected_services = _unexpected_entries(SESSIONS_ROOT / "services", SESSIONS_SERVICES_ALLOWED_ENTRIES)

        assert not unexpected, f"Unexpected entries under src/dovo/core/sessions: {unexpected}"
        assert not unexpected_services, (
            f"Unexpected entries under src/dovo/core/sessions/services: {unexpected_services}"
        )

    def test_unexpected_entries_flags_subpackage_and_ignores_pycache(self, tmp_path: Path) -> None:
        """[tier-4/unit] _unexpected_entries: a tmp tree containing diff/, facade.py, and __pycache__/ beside allowed names -> returns exactly ['diff', 'facade.py']."""
        for name in ("diff", "__pycache__", "services"):
            (tmp_path / name).mkdir()
        for name in ("facade.py", "models.py"):
            (tmp_path / name).write_text("", encoding="utf-8")

        assert _unexpected_entries(tmp_path, SESSIONS_ALLOWED_ENTRIES) == ["diff", "facade.py"]

    def test_retired_core_packages_are_gone(self) -> None:
        """[tier-4/unit] src/dovo/core and tests/core: no directory named patch, diff, history, logs, or doctor exists; and no import under src/ or tests/ targets dovo.core.{patch,diff,history,logs,doctor}."""
        assert CORE_ROOT.is_dir()
        assert CORE_TESTS_ROOT.is_dir()
        source_files = collect_python_files(SRC_ROOT) + collect_python_files(TESTS_ROOT)
        assert source_files

        lingering_dirs = [
            str((root / name).relative_to(REPO_ROOT))
            for root in (CORE_ROOT, CORE_TESTS_ROOT)
            for name in RETIRED_CORE_PACKAGES
            if (root / name).exists()
        ]
        retired_imports = [
            f"{file_path.relative_to(REPO_ROOT)}:{line}: imports '{module}'"
            for file_path in source_files
            for module, line in _imported_modules(file_path)
            for name in RETIRED_CORE_PACKAGES
            if module == f"dovo.core.{name}" or module.startswith(f"dovo.core.{name}.")
        ]

        assert not lingering_dirs, "Retired core package directories still exist:\n" + "\n".join(lingering_dirs)
        assert not retired_imports, "Found imports of retired core packages:\n" + "\n".join(retired_imports)
