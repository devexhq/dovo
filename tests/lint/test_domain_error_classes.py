"""Invariant: each package in src/dovo defines at most one DomainError class, and no class derives from one.

Scope: classes are matched by the simple name of each base (`DomainError`, `GitError`, `errors.GitError`), across
every module in src/dovo, so a base imported from another package is still resolved. A package is `core/<name>` or
`cli/<name>` for code under those folders, and the top-level folder (`engine`, `common`) otherwise. Not detected: a
base written as a call or subscript, a class created with `type(...)`, and a base imported under an alias
(`from dovo.core.git import GitError as X`, then `class Y(X)`). Because classes are matched by name only, an unrelated
class that reuses a DomainError class's name (`class EngineError(Exception)` elsewhere) is counted as one.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files, package_of

ENTRY: Final[str] = "docs/agents/architecture/exceptions.md#one-domainerror-class-per-package"
ROOT_CLASS: Final[str] = "DomainError"

_DERIVED_FIXTURE: Final[str] = """
class GitError(DomainError):
    pass

class GitTimeoutError(GitError):
    pass
"""

_TWO_CLASSES_FIXTURE: Final[str] = """
class GitError(DomainError):
    pass

class GitPatchError(DomainError):
    pass
"""

_ONE_CLASS_FIXTURE: Final[str] = """
class GitError(DomainError):
    pass

class MalformedDiffHeader(Exception):
    pass
"""


def _base_name(base: ast.expr) -> str | None:
    """Return the simple name a base expression refers to, or None for a call, subscript or other form."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


@dataclass(frozen=True, slots=True)
class _ClassDef:
    """A class definition found in src/dovo: where it is, its name and the simple names of its bases."""

    rel_path: str
    line: int
    name: str
    bases: tuple[str, ...]


def _classes(modules: dict[str, str]) -> list[_ClassDef]:
    """Return every class defined in modules, which maps a repo-relative path to its source."""
    return [
        _ClassDef(rel_path, node.lineno, node.name, tuple(name for name in map(_base_name, node.bases) if name))
        for rel_path, source in modules.items()
        for node in ast.walk(ast.parse(source, filename=rel_path))
        if isinstance(node, ast.ClassDef)
    ]


def _domain_error_classes(classes: list[_ClassDef]) -> set[str]:
    """Return the names of every class that derives from DomainError, directly or through another class."""
    derived: set[str] = set()
    pending = list(classes)
    while found := [cls for cls in pending if any(base == ROOT_CLASS or base in derived for base in cls.bases)]:
        derived.update(cls.name for cls in found)
        pending = [cls for cls in pending if cls.name not in derived]
    return derived


def _subclass_violations(classes: list[_ClassDef], derived: set[str]) -> list[str]:
    """Return '<path>:<line>: <name> derives from the DomainError class <base>' for each such base."""
    return [
        f"{cls.rel_path}:{cls.line}: {cls.name} derives from the DomainError class {base}"
        for cls in classes
        for base in cls.bases
        if base in derived
    ]


def _package_violations(classes: list[_ClassDef], derived: set[str]) -> list[str]:
    """Return '<path>:<line>: <package> defines <n> DomainError classes (<names>)' for each class after a package's first."""
    per_package: dict[str, list[_ClassDef]] = defaultdict(list)
    for cls in classes:
        if cls.name in derived:
            per_package[package_of(cls.rel_path)].append(cls)
    return [
        f"{extra.rel_path}:{extra.line}: {package} defines {len(defined)} DomainError classes "
        f"({', '.join(cls.name for cls in defined)})"
        for package, defined in per_package.items()
        for extra in defined[1:]
    ]


def _violations(modules: dict[str, str]) -> list[str]:
    """Return every class deriving from a DomainError class and every package class after the first, sorted."""
    classes = _classes(modules)
    derived = _domain_error_classes(classes)
    return sorted([*_subclass_violations(classes, derived), *_package_violations(classes, derived)])


class DomainErrorClassTests:
    def test_src_defines_one_domain_error_class_per_package_with_no_subclasses(self) -> None:
        """Report no class deriving from a DomainError class and no package defining two in src/dovo."""
        files = collect_python_files(SRC_ROOT)
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"
        modules = {str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8") for path in files}

        assert _violations(modules) == [], (
            f"raise the package's one DomainError class with its ErrorCode and branch on error_code; see {ENTRY}"
        )

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(
                _DERIVED_FIXTURE,
                [
                    "src/dovo/core/git/exceptions.py:5: GitTimeoutError derives from the DomainError class GitError",
                    "src/dovo/core/git/exceptions.py:5: core/git defines 2 DomainError classes "
                    "(GitError, GitTimeoutError)",
                ],
                id="derived",
            ),
            pytest.param(
                _TWO_CLASSES_FIXTURE,
                ["src/dovo/core/git/exceptions.py:5: core/git defines 2 DomainError classes (GitError, GitPatchError)"],
                id="two-classes",
            ),
            pytest.param(_ONE_CLASS_FIXTURE, [], id="one-class"),
        ],
    )
    def test_rule_flags_subclasses_and_second_classes(self, source: str, expected: list[str]) -> None:
        """Return expected from _violations for source placed at src/dovo/core/git/exceptions.py."""
        assert _violations({"src/dovo/core/git/exceptions.py": source}) == expected

    def test_rule_resolves_a_base_defined_in_another_package(self) -> None:
        """Flag a class in core/worktree deriving from GitError defined in core/git."""
        modules = {
            "src/dovo/core/git/exceptions.py": "class GitError(DomainError):\n    pass\n",
            "src/dovo/core/worktree/exceptions.py": "class WorktreeGitError(errors.GitError):\n    pass\n",
        }

        assert _violations(modules) == [
            "src/dovo/core/worktree/exceptions.py:1: WorktreeGitError derives from the DomainError class GitError"
        ]
