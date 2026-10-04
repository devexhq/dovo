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
GIT_ROOT: Final[Path] = CORE_ROOT / "git"
SESSIONS_ROOT: Final[Path] = CORE_ROOT / "sessions"
SESSIONS_DIFF_ROOT: Final[Path] = SESSIONS_ROOT / "diff"
SESSIONS_HISTORY_ROOT: Final[Path] = SESSIONS_ROOT / "history"
SESSIONS_LOGS_ROOT: Final[Path] = SESSIONS_ROOT / "logs"
RETIRED_CORE_PACKAGES: Final[tuple[str, ...]] = ("patch", "diff", "history", "logs", "doctor")
CATALOG_BANNED_PREFIXES: Final[tuple[str, ...]] = (
    "dovo.core.agents",
    "dovo.core.sessions.logs",
    "dovo.core.sessions.history",
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
        """[tier-4/unit] src/dovo/core/catalog: no module imports dovo.core.agents, .sessions.logs, .sessions.history, dovo.engine, or dovo.cli; violations list is empty."""
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

    def test_git_never_imports_other_core_packages(self) -> None:
        """[tier-4/unit] src/dovo/core/git: no module imports a dovo.core.<pkg> other than dovo.core.git; violation list is empty."""
        assert collect_python_files(GIT_ROOT)

        violations = [
            f"{file_path.relative_to(REPO_ROOT)}:{line}: imports '{module}'"
            for file_path in collect_python_files(GIT_ROOT)
            for module, line in _imported_modules(file_path)
            if module.startswith("dovo.core.") and module != "dovo.core.git" and not module.startswith("dovo.core.git.")
        ]

        assert not violations, "Found prohibited imports of other core packages in src/dovo/core/git:\n" + "\n".join(
            violations
        )

    def test_sessions_package_exports_nothing(self) -> None:
        """[tier-4/unit] src/dovo/core/sessions/__init__.py: module body is exactly one docstring expression (no imports, no __all__, no assignments)."""
        init_path = SESSIONS_ROOT / "__init__.py"
        assert init_path.is_file()

        body = ast.parse(init_path.read_text(encoding="utf-8")).body

        assert len(body) == 1
        statement = body[0]
        assert isinstance(statement, ast.Expr)
        assert isinstance(statement.value, ast.Constant)
        assert isinstance(statement.value.value, str)

    def test_sessions_logs_never_imports_history_or_diff(self) -> None:
        """[tier-4/unit] src/dovo/core/sessions/logs: no module imports dovo.core.sessions.history or dovo.core.sessions.diff; violation list is empty."""
        assert collect_python_files(SESSIONS_LOGS_ROOT)

        violations: list[str] = []
        for banned_prefix in ("dovo.core.sessions.history", "dovo.core.sessions.diff"):
            violations.extend(_violations_under(SESSIONS_LOGS_ROOT, banned_prefix))

        assert not violations, "Found prohibited imports in src/dovo/core/sessions/logs:\n" + "\n".join(violations)

    def test_sessions_diff_never_imports_history_or_logs(self) -> None:
        """[tier-4/unit] src/dovo/core/sessions/diff: no module imports dovo.core.sessions.history or dovo.core.sessions.logs; violation list is empty."""
        assert collect_python_files(SESSIONS_DIFF_ROOT)

        violations: list[str] = []
        for banned_prefix in ("dovo.core.sessions.history", "dovo.core.sessions.logs"):
            violations.extend(_violations_under(SESSIONS_DIFF_ROOT, banned_prefix))

        assert not violations, "Found prohibited imports in src/dovo/core/sessions/diff:\n" + "\n".join(violations)

    def test_sessions_history_never_imports_diff(self) -> None:
        """[tier-4/unit] src/dovo/core/sessions/history: no module imports dovo.core.sessions.diff; violation list is empty."""
        assert collect_python_files(SESSIONS_HISTORY_ROOT)

        violations = _violations_under(SESSIONS_HISTORY_ROOT, "dovo.core.sessions.diff")

        assert not violations, (
            "Found prohibited dovo.core.sessions.diff imports in src/dovo/core/sessions/history:\n"
            + "\n".join(violations)
        )

    def test_sessions_history_imports_logs_only_through_public_exports(self) -> None:
        """[tier-4/unit] src/dovo/core/sessions/history: every import of dovo.core.sessions.logs names the bare package; any 'dovo.core.sessions.logs.<submodule>' import is a violation; history.py has at least one bare import."""
        logs_package = "dovo.core.sessions.logs"
        history_files = collect_python_files(SESSIONS_HISTORY_ROOT)
        assert history_files

        imports = [
            (file_path, module, line)
            for file_path in history_files
            for module, line in _imported_modules(file_path)
            if module == logs_package or module.startswith(f"{logs_package}.")
        ]
        violations = [
            f"{file_path.relative_to(REPO_ROOT)}:{line}: imports '{module}'"
            for file_path, module, line in imports
            if module != logs_package
        ]

        assert any(file_path.name == "history.py" and module == logs_package for file_path, module, _ in imports)
        assert not violations, (
            "Found deep dovo.core.sessions.logs imports in src/dovo/core/sessions/history:\n" + "\n".join(violations)
        )

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
