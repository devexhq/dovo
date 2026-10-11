"""Invariant: each package imports only from the layers below it, per the table in docs/agents/architecture.md."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import (
    REPO_ROOT,
    SRC_ROOT,
    check_import_from_node,
    check_import_node,
    collect_python_files,
)

CORE_ROOT: Final[Path] = SRC_ROOT / "core"
ENGINE_ROOT: Final[Path] = SRC_ROOT / "engine"
NON_PACKAGE_DIRS: Final[frozenset[str]] = frozenset({"__pycache__", "docs"})


@dataclass(frozen=True)
class LayerRule:
    """One package and the import prefixes it must never use."""

    root: Path
    banned: tuple[str, ...]


def _other_core_packages(keep: str) -> tuple[str, ...]:
    """Return the dotted names of every core package except `keep`."""
    return tuple(
        f"dovo.core.{entry.name}"
        for entry in sorted(CORE_ROOT.iterdir())
        if entry.is_dir() and entry.name not in NON_PACKAGE_DIRS and entry.name != keep
    )


def _engine_modules_outside_executors() -> tuple[str, ...]:
    """Return the dotted names of every engine module or package except `executors`."""
    return tuple(
        f"dovo.engine.{entry.stem}"
        for entry in sorted(ENGINE_ROOT.iterdir())
        if entry.name not in NON_PACKAGE_DIRS and entry.stem not in {"__init__", "executors", "__pycache__"}
    )


# Known violations carried until fixed: (file, imported module). Entries are only ever removed, never added.
LAYER_EXCEPTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # Annotation-only import under TYPE_CHECKING in common/filesystem/models.py.
        ("src/dovo/common/filesystem/models.py", "dovo.core.catalog.models"),
        ("src/dovo/engine/executors/agent_step.py", "dovo.engine.session_log"),
    }
)

LAYER_RULES: Final[dict[str, LayerRule]] = {
    "common": LayerRule(SRC_ROOT / "common", ("dovo.core", "dovo.engine", "dovo.cli")),
    "core": LayerRule(CORE_ROOT, ("dovo.engine", "dovo.cli", "rich")),
    "engine": LayerRule(ENGINE_ROOT, ("dovo.cli",)),
    "engine_executors": LayerRule(ENGINE_ROOT / "executors", ("dovo.cli", *_engine_modules_outside_executors())),
    "core_project": LayerRule(CORE_ROOT / "project", _other_core_packages("project")),
    "core_git": LayerRule(CORE_ROOT / "git", _other_core_packages("git")),
    "core_inputs": LayerRule(CORE_ROOT / "inputs", ("dovo.core.catalog", "dovo.core.agents")),
    "core_catalog": LayerRule(
        CORE_ROOT / "catalog", ("dovo.core.agents", "dovo.core.sessions", "dovo.engine", "dovo.cli")
    ),
    "core_artifacts": LayerRule(CORE_ROOT / "artifacts", ("dovo.engine",)),
    "core_agents": LayerRule(CORE_ROOT / "agents", ("dovo.core.config", "dovo.engine")),
    "core_sessions": LayerRule(
        CORE_ROOT / "sessions",
        ("dovo.core.agents", "dovo.core.worktree", "dovo.core.catalog", "dovo.engine", "dovo.cli"),
    ),
}


def _violation_key(violation: str) -> tuple[str, str]:
    """Return (file, imported module) from a formatted violation string."""
    return violation.split(":", 1)[0], violation.split("'")[1]


def _violations(file_path: Path, banned_prefix: str) -> list[str]:
    """Return the banned-import violations in one file."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = file_path.relative_to(REPO_ROOT)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(check_import_node(node, banned_prefix, rel_path))
        elif isinstance(node, ast.ImportFrom):
            found.extend(check_import_from_node(node, banned_prefix, rel_path))
    return found


class LayerDirectionTests:
    """Every package in LAYER_RULES imports nothing from its banned prefixes."""

    @pytest.mark.parametrize("rule", [pytest.param(rule, id=name) for name, rule in LAYER_RULES.items()])
    def test_package_imports_no_banned_prefix(self, rule: LayerRule) -> None:
        """No module under the rule's root imports any of its banned prefixes."""
        files = collect_python_files(rule.root)
        assert files, f"{rule.root} contains no Python files, so the rule would pass vacuously"

        violations = [
            found
            for file_path in files
            for prefix in rule.banned
            for found in _violations(file_path, prefix)
            if _violation_key(found) not in LAYER_EXCEPTIONS
        ]

        assert violations == []

    @pytest.mark.parametrize("exception", [pytest.param(item, id=item[0]) for item in sorted(LAYER_EXCEPTIONS)])
    def test_layer_exception_still_occurs(self, exception: tuple[str, str]) -> None:
        """Every allowlisted violation still exists, so a fixed one must be removed from the list."""
        file_path, module = exception
        found = {
            _violation_key(violation)
            for rule in LAYER_RULES.values()
            for prefix in rule.banned
            for violation in _violations(REPO_ROOT / file_path, prefix)
        }

        assert exception in found, f"{module} is no longer imported by {file_path}; delete it from LAYER_EXCEPTIONS"

    @pytest.mark.parametrize(
        ("keep", "expected"),
        [
            pytest.param("project", "dovo.core.git", id="includes_sibling"),
            pytest.param("git", "dovo.core.project", id="includes_other_sibling"),
        ],
    )
    def test_other_core_packages_lists_siblings_but_not_itself(self, keep: str, expected: str) -> None:
        """The derived ban list names sibling packages and excludes the package being checked."""
        banned = _other_core_packages(keep)

        assert expected in banned
        assert f"dovo.core.{keep}" not in banned

    def test_engine_modules_outside_executors_excludes_executors_itself(self) -> None:
        """The executors ban list names engine modules but never the executors package."""
        banned = _engine_modules_outside_executors()

        assert "dovo.engine.coordinator" in banned
        assert "dovo.engine.executors" not in banned
