"""Invariant: every ErrorCode member is named <PREFIX>_<NOUN>_<STATE>, is referenced in src/dovo, and is raised only by
the packages its prefix names or the packages listed as sharing it.

Scope: the name check reads the real `ErrorCode` enum. The reference check collects every `ErrorCode.<NAME>` attribute
in src/dovo outside `common/error_codes.py`. The raising check reads every call in src/dovo to a failure constructor
whose first positional argument, or whose `error_code=` keyword, is an `ErrorCode.<NAME>` attribute, and maps its file
to a package with `package_of`. A failure constructor is a callee whose simple name ends in `Error`, `_error` or
`Result`, or is `Failure` (`WorktreeError(...)`, `git_command_error(...)`, `CatalogValidateResult(...)`); any other
call taking a code, such as `_LABELS.get(ErrorCode.X)`, is a lookup, not a raise.

Not detected, and left to review: a code passed through a variable (`WorktreeError(exc.error_code, ...)`, the intended
pass-through of a wrapped failure), a code built from a string, a code that is neither a call's first argument nor its
`error_code=` value (`error_code=ErrorCode.X if errors else None`), a code raised through a callee named otherwise,
an `ErrorCode` imported under an alias (`from dovo.common.error_codes import ErrorCode as EC`), whether a `_FAILED`
name uses the verb's base form (`WORKTREE_CREATION_FAILED` would pass the name check).

`SHARED` may hold only the entries in `SHARED_ALLOWED`, the two on main, which #1244 removes; the table check reports
any other.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from typing import Final

import pytest

from dovo.common.error_codes import ErrorCode
from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files, package_of

ENTRY: Final[str] = "docs/agents/architecture/exceptions.md#errorcode-naming"
ENUM_PATH: Final[str] = "src/dovo/common/error_codes.py"
NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]+(_[A-Z]+)+$")
EXCEPTIONS: Final[frozenset[str]] = frozenset({"INTERNAL_ERROR"})

OWNERS: Final[dict[str, frozenset[str]]] = {
    "INTERNAL": frozenset({"common"}),
    "GIT": frozenset({"core/git"}),
    "PATCH": frozenset({"core/git"}),
    "AGENT": frozenset({"core/agents"}),
    "WORKTREE": frozenset({"core/worktree"}),
    "CATALOG": frozenset({"core/catalog"}),
    "CONFIG": frozenset({"core/config"}),
    "BOOTSTRAP": frozenset({"core/bootstrap"}),
    "PROJECT": frozenset({"core/project"}),
    "SESSION": frozenset({"engine"}),
    "BLUEPRINT": frozenset({"engine"}),
    "INTERACTION": frozenset({"engine"}),
}

SHARED: Final[dict[str, frozenset[str]]] = {
    "GIT_FAILED": frozenset({"core/agents", "core/worktree"}),
    "GIT_TIMEOUT": frozenset({"core/agents"}),
}

SHARED_ALLOWED: Final[dict[str, frozenset[str]]] = {
    "GIT_FAILED": frozenset({"core/agents", "core/worktree"}),
    "GIT_TIMEOUT": frozenset({"core/agents"}),
}

STATES: Final[tuple[str, ...]] = (
    "NOT_FOUND",
    "NOT_RESUMABLE",
    "NOT_PENDING",
    "MISSING",
    "INVALID",
    "CORRUPT",
    "INCONSISTENT",
    "CONFLICT",
    "EXCEEDED",
    "FAILED",
    "TIMEOUT",
    "UNREADABLE",
    "UNAVAILABLE",
    "EXISTS",
    "PROTECTED",
    "FORBIDDEN",
    "MERGED",
    "EMPTY",
    "DIRTY",
    "DISALLOWED",
    "UNSAFE",
    "UNWRITABLE",
)

_FOREIGN_RAISE_FIXTURE: Final[str] = """
def check(worktree_id):
    raise EngineError(ErrorCode.WORKTREE_MISSING, f"Worktree '{worktree_id}' is missing.")
"""

_PASS_THROUGH_FIXTURE: Final[str] = """
def wrap(worktree_id):
    try:
        run()
    except GitError as exc:
        raise WorktreeError(exc.error_code, f"Git failed for worktree '{worktree_id}': {exc}") from exc.__cause__
"""

_SHARED_RAISE_FIXTURE: Final[str] = """
def check(completed):
    if completed.returncode:
        raise AgentError(ErrorCode.GIT_FAILED, "git reset failed")
    return Failure(error_code=ErrorCode.AGENT_SCRATCH_UNAVAILABLE, message="no scratch")
"""

_LOOKUP_FIXTURE: Final[str] = """
def label(status):
    if status is None:
        return _LABELS.get(ErrorCode.WORKTREE_MISSING)
    return describe(ErrorCode.GIT_FAILED, error_code=ErrorCode.SESSION_NOT_FOUND)
"""


def _name_violation(name: str, value: str) -> str | None:
    """Return the reason name breaks the naming pattern, or None when it follows it."""
    if value != name:
        return f"value {value!r} differs from the name"
    if not NAME_PATTERN.match(name):
        return "is not upper-case words joined by underscores"
    prefix = name.split("_", 1)[0]
    if prefix not in OWNERS:
        return f"prefix {prefix} has no entry in OWNERS"
    if not any(name.endswith(f"_{state}") for state in STATES):
        return "does not end in a state from STATES"
    return None


def _name_violations(members: list[tuple[str, str]]) -> list[str]:
    """Return '<NAME>: <reason>' for each (name, value) pair outside EXCEPTIONS that breaks the naming pattern."""
    reasons = ((name, _name_violation(name, value)) for name, value in members if name not in EXCEPTIONS)
    return [f"{name}: {reason}" for name, reason in reasons if reason is not None]


def _code_name(node: ast.expr) -> str | None:
    """Return X when node is the attribute `ErrorCode.X`, else None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ErrorCode":
        return node.attr
    return None


def _is_failure_constructor(call: ast.Call) -> bool:
    """Return whether call's callee is named like a failure constructor: *Error, *_error, *Result or Failure."""
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name == "Failure" or name.endswith(("Error", "_error", "Result"))


def _literal_codes(call: ast.Call) -> Iterator[str]:
    """Yield the ErrorCode member names a failure constructor call passes as its first argument or as error_code=."""
    if not _is_failure_constructor(call):
        return
    candidates = [*call.args[:1], *(keyword.value for keyword in call.keywords if keyword.arg == "error_code")]
    for candidate in candidates:
        name = _code_name(candidate)
        if name is not None:
            yield name


def _raised_codes(modules: dict[str, str]) -> Iterator[tuple[str, int, str]]:
    """Yield (rel_path, line, NAME) for each literal ErrorCode passed to a call in modules."""
    for rel_path, source in modules.items():
        for node in ast.walk(ast.parse(source, filename=rel_path)):
            if isinstance(node, ast.Call):
                yield from ((rel_path, node.lineno, name) for name in _literal_codes(node))


def _may_raise(package: str, name: str) -> bool:
    """Return whether package owns the prefix of name or is listed as sharing name."""
    prefix = name.split("_", 1)[0]
    return package in OWNERS.get(prefix, frozenset()) or package in SHARED.get(name, frozenset())


def _raise_violations(modules: dict[str, str]) -> list[str]:
    """Return '<path>:<line>: <package> raises <NAME>, whose prefix <P> belongs to <owners>' per foreign literal raise."""
    violations: list[str] = []
    for rel_path, line, name in _raised_codes(modules):
        package = package_of(rel_path)
        if not _may_raise(package, name):
            prefix = name.split("_", 1)[0]
            owners = ", ".join(sorted(OWNERS.get(prefix, frozenset()))) or "no package"
            violations.append(f"{rel_path}:{line}: {package} raises {name}, whose prefix {prefix} belongs to {owners}")
    return sorted(violations)


def _new_shared_entries(shared: dict[str, frozenset[str]]) -> list[str]:
    """Return '<NAME>: <package>' for each (name, package) pair in shared that SHARED_ALLOWED does not hold."""
    return [
        f"{name}: {package}"
        for name, packages in shared.items()
        for package in sorted(packages)
        if package not in SHARED_ALLOWED.get(name, frozenset())
    ]


def _referenced(modules: dict[str, str]) -> set[str]:
    """Return every X written as `ErrorCode.X` in modules."""
    return {
        name
        for rel_path, source in modules.items()
        for node in ast.walk(ast.parse(source, filename=rel_path))
        if isinstance(node, ast.expr) and (name := _code_name(node)) is not None
    }


def _src_modules() -> dict[str, str]:
    """Return every module under src/dovo except the ErrorCode enum, keyed by repo-relative path."""
    files = collect_python_files(SRC_ROOT)
    assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously. See {ENTRY}"
    modules = {str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8") for path in files}
    modules.pop(ENUM_PATH)
    return modules


class ErrorCodeNameTests:
    def test_every_member_follows_the_naming_pattern(self) -> None:
        """Report no ErrorCode member outside EXCEPTIONS that breaks <PREFIX>_<NOUN>_<STATE>."""
        violations = _name_violations([(member.name, member.value) for member in ErrorCode])

        assert violations == [], f"name the code <PREFIX>_<NOUN>_<STATE>. See {ENTRY}"

    @pytest.mark.parametrize(
        ("members", "expected"),
        [
            pytest.param(
                [("PROJECT_IDENTITY_INVALID_SCHEMA", "PROJECT_IDENTITY_INVALID_SCHEMA")],
                ["PROJECT_IDENTITY_INVALID_SCHEMA: does not end in a state from STATES"],
                id="state-first",
            ),
            pytest.param(
                [("FS_NOT_FOUND", "FS_NOT_FOUND")], ["FS_NOT_FOUND: prefix FS has no entry in OWNERS"], id="prefix"
            ),
            pytest.param(
                [("GIT_FAILED", "git_failed")], ["GIT_FAILED: value 'git_failed' differs from the name"], id="value"
            ),
            pytest.param([("INTERNAL_ERROR", "INTERNAL_ERROR"), ("GIT_FAILED", "GIT_FAILED")], [], id="compliant"),
        ],
    )
    def test_rule_flags_names_that_break_the_pattern(self, members: list[tuple[str, str]], expected: list[str]) -> None:
        """Return expected from _name_violations for members."""
        assert _name_violations(members) == expected


class ErrorCodeReferenceTests:
    def test_every_member_is_referenced_in_src(self) -> None:
        """Report no ErrorCode member that no module in src/dovo names as ErrorCode.<NAME>."""
        referenced = _referenced(_src_modules())

        unreferenced = [
            f"{member.name} is not referenced in src/" for member in ErrorCode if member.name not in referenced
        ]

        assert unreferenced == [], f"delete the member, or raise it where the failure happens. See {ENTRY}"


class ErrorCodeRaiserTests:
    def test_src_raises_each_literal_code_from_a_package_its_prefix_names(self) -> None:
        """Report no literal ErrorCode passed to a call from a package that neither owns its prefix nor shares it."""
        violations = _raise_violations(_src_modules())

        assert violations == [], (
            f"raise a code under the package's own prefix, or pass the wrapped code through. See {ENTRY}"
        )

    @pytest.mark.parametrize(
        ("rel_path", "source", "expected"),
        [
            pytest.param(
                "src/dovo/engine/loader.py",
                _FOREIGN_RAISE_FIXTURE,
                [
                    "src/dovo/engine/loader.py:3: engine raises WORKTREE_MISSING, whose prefix WORKTREE belongs to core/worktree"
                ],
                id="foreign",
            ),
            pytest.param("src/dovo/core/worktree/services/patch.py", _PASS_THROUGH_FIXTURE, [], id="pass-through"),
            pytest.param("src/dovo/core/agents/mutation_git.py", _SHARED_RAISE_FIXTURE, [], id="shared"),
            pytest.param(
                "src/dovo/core/catalog/catalog.py",
                _SHARED_RAISE_FIXTURE,
                [
                    "src/dovo/core/catalog/catalog.py:4: core/catalog raises GIT_FAILED, whose prefix GIT belongs to core/git",
                    "src/dovo/core/catalog/catalog.py:5: core/catalog raises AGENT_SCRATCH_UNAVAILABLE, whose prefix "
                    "AGENT belongs to core/agents",
                ],
                id="unlisted-sharer",
            ),
            pytest.param("src/dovo/cli/ui/formatters/labels.py", _LOOKUP_FIXTURE, [], id="lookup-not-raise"),
        ],
    )
    def test_rule_flags_foreign_literal_raises(self, rel_path: str, source: str, expected: list[str]) -> None:
        """Return expected from _raise_violations for source placed at rel_path."""
        assert _raise_violations({rel_path: source}) == expected


class ErrorCodeTableTests:
    def test_every_owners_prefix_names_a_member(self) -> None:
        """Report no OWNERS prefix that no ErrorCode member starts with."""
        prefixes = {member.name.split("_", 1)[0] for member in ErrorCode}

        assert sorted(set(OWNERS) - prefixes) == [], f"delete the OWNERS entry with no member. See {ENTRY}"

    def test_every_shared_entry_is_a_member_its_sharers_raise(self) -> None:
        """Report no SHARED code that is not a member, and no listed sharer that never raises it as a literal."""
        raised = {(package_of(rel_path), name) for rel_path, _, name in _raised_codes(_src_modules())}

        dead = [
            f"{name}: {package}"
            for name, packages in SHARED.items()
            for package in sorted(packages)
            if name not in ErrorCode.__members__ or (package, name) not in raised
        ]

        assert dead == [], f"delete the SHARED entry no package raises. See {ENTRY}"

    def test_shared_lists_no_entry_beyond_the_two_on_main(self) -> None:
        """Report no SHARED entry that SHARED_ALLOWED does not hold."""
        assert _new_shared_entries(SHARED) == [], (
            f"raise a code under the package's own prefix instead of sharing another package's code. See {ENTRY}"
        )

    @pytest.mark.parametrize(
        ("shared", "expected"),
        [
            pytest.param(
                {**SHARED_ALLOWED, "PATCH_APPLY_FAILED": frozenset({"core/agents"})},
                ["PATCH_APPLY_FAILED: core/agents"],
                id="new-code",
            ),
            pytest.param(
                {**SHARED_ALLOWED, "GIT_FAILED": frozenset({"core/agents", "core/catalog", "core/worktree"})},
                ["GIT_FAILED: core/catalog"],
                id="new-sharer",
            ),
            pytest.param(SHARED_ALLOWED, [], id="on-main"),
        ],
    )
    def test_rule_flags_shared_entries_beyond_the_two_on_main(
        self, shared: dict[str, frozenset[str]], expected: list[str]
    ) -> None:
        """Return expected from _new_shared_entries for shared."""
        assert _new_shared_entries(shared) == expected
