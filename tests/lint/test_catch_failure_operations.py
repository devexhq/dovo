"""Invariant: every diagnostic operation name in src/dovo starts with the calling module's package folder, and every
catch_failure operation is `<package>.<entrypoint>` and unique within its module.

Scope: the rule reads every call to `catch_failure` (a bare name or an attribute) whose operation, the first argument
or the `operation=` keyword, is a string literal, and the string-literal `operation=` keyword of every call to a
recorder in RECORDERS: `record_exception` and the recording helpers that forward `operation` to it. The package is the
last folder of `package_of` for a module under `core/`, the top-level folder otherwise, and `cli` for every module under
`cli/`: `agents` for `core/agents`, `engine` for `engine`, `cli` for `cli/session/commands`. A catch_failure operation
must match `^<package>\\.[a-z_]+$` and appear once per module; a recorder's operation only has to start with
`<package>.`, because a recording site may name the step that records and may repeat.

Not detected, and left to review: an operation passed through a variable or built from an f-string, `catch_failure` or
a recorder imported under an alias, a new recording helper missing from RECORDERS, and whether the part after the
package names the entrypoint whose result reports the failure rather than a sub-step.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterator
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files, package_of

ENTRY: Final[str] = "docs/agents/architecture/exceptions.md#catch_failure-operation-names"
CATCH: Final[str] = "catch_failure"
RECORDERS: Final[frozenset[str]] = frozenset(
    {"record_exception", "_record_recovered", "_record_reported", "_require_present"}
)
NAME_TAIL: Final[str] = r"\.[a-z_]+$"

_CROSS_PACKAGE_FIXTURE: Final[str] = """
def execute(self):
    with catch_failure("catalog.load_definition", step=self.step.uses) as caught:
        resolved = self._resolve_step()
"""

_SINGULAR_FIXTURE: Final[str] = """
def invoke(self, request):
    with catch_failure("agent.invoke") as caught:
        validate(request)
"""

_REPEATED_FIXTURE: Final[str] = """
def execute(self):
    with catch_failure("engine.resume") as caught:
        target = self._target()
    with catch_failure("engine.resume") as caught:
        outcome = self._resume(target)
"""

_ATTRIBUTE_FIXTURE: Final[str] = """
def execute(self):
    with exceptions.catch_failure("engine.Run") as caught:
        outcome = self._run()
"""

_RECORD_FIXTURE: Final[str] = """
def reconcile(exc):
    record_exception(exc, level=DiagnosticLevel.ERROR, operation="session.reconcile")
    record_exception(exc, level=DiagnosticLevel.ERROR, operation="engine.persist")
    record_exception(exc, level=DiagnosticLevel.DEBUG, operation="engine.persist")
"""

_KEYWORD_CATCH_FIXTURE: Final[str] = """
def execute(self):
    with catch_failure(operation="catalog.load_definition") as caught:
        target = self._target()
    with catch_failure(operation="catalog.load_definition") as caught:
        outcome = self._resume(target)
"""

_HELPER_FIXTURE: Final[str] = """
def cleanup(self, exc, record):
    _record_recovered(exc, operation="worktrees.cleanup", context={})
    self._require_present(record, operation="git.diff", missing_message="missing")
"""

_UNRELATED_KEYWORD_FIXTURE: Final[str] = """
def save(client):
    client.some_api(operation="upsert")
"""

_CLI_FIXTURE: Final[str] = """
def run(exc):
    record_exception(exc, level=DiagnosticLevel.ERROR, operation="cli.run")
    record_exception(exc, level=DiagnosticLevel.ERROR, operation="session.run")
"""

_COMPLIANT_FIXTURE: Final[str] = """
def execute(self, operation):
    with catch_failure("engine.resume_target") as caught:
        target = self._target()
    with catch_failure("engine.resume") as caught:
        outcome = self._resume(target)
    with catch_failure(operation) as caught:
        pass
    record_exception(exc, level=DiagnosticLevel.ERROR, operation=operation)
"""


def _callee(call: ast.Call) -> str | None:
    """Return the simple name call invokes, bare or as an attribute."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _literal(node: ast.expr | None) -> str | None:
    """Return node's value when it is a string literal, else None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _operation_arg(call: ast.Call, *, positional: bool) -> str | None:
    """Return call's literal operation: its first argument when positional and present, else its operation= keyword."""
    if positional and call.args:
        return _literal(call.args[0])
    return next((_literal(kw.value) for kw in call.keywords if kw.arg == "operation"), None)


def _operations(calls: list[ast.Call], callees: frozenset[str], *, positional: bool) -> list[tuple[int, str]]:
    """Return (line, operation) for each call to one of callees whose operation is a string literal."""
    named = ((call.lineno, _operation_arg(call, positional=positional)) for call in calls if _callee(call) in callees)
    return [(line, name) for line, name in named if name is not None]


def _package(rel_path: str) -> str:
    """Return the operation package of a src/dovo module: `cli` for all of cli/, else the last folder of package_of."""
    package = package_of(rel_path)
    return "cli" if package.startswith("cli") else package.rsplit("/", 1)[-1]


def _catch_problems(catches: list[tuple[int, str]], package: str) -> Iterator[tuple[int, str]]:
    """Yield (line, problem) for each catch_failure operation off the pattern or repeated in the module."""
    pattern = re.compile(rf"^{re.escape(package)}{NAME_TAIL}")
    counts = Counter(name for _, name in catches)
    for line, name in catches:
        if pattern.fullmatch(name) is None:
            yield line, f"catch_failure operation '{name}' is not '{package}.<entrypoint>'"
        if counts[name] > 1:
            yield line, f"catch_failure operation '{name}' repeats in this module"


def _violations(source: str, rel_path: str) -> list[str]:
    """Return '<rel_path>:<line>: <problem>' for each operation that breaks the naming rule."""
    package = _package(rel_path)
    calls = [node for node in ast.walk(ast.parse(source, filename=rel_path)) if isinstance(node, ast.Call)]
    problems = [*_catch_problems(_operations(calls, frozenset({CATCH}), positional=True), package)]
    problems += [
        (line, f"operation '{name}' does not start with '{package}.'")
        for line, name in _operations(calls, RECORDERS, positional=False)
        if not name.startswith(f"{package}.")
    ]
    return [f"{rel_path}:{line}: {problem}" for line, problem in sorted(problems)]


class CatchFailureOperationTests:
    def test_src_operations_follow_the_naming_rule(self) -> None:
        """Report no operation name in src/dovo that breaks the package, pattern or uniqueness rule."""
        files = collect_python_files(SRC_ROOT)
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        violations = sorted(
            violation
            for path in files
            for violation in _violations(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
        )

        assert violations == [], f"name each operation <package>.<entrypoint>; see {ENTRY}"

    @pytest.mark.parametrize(
        ("source", "rel_path", "expected"),
        [
            pytest.param(
                _CROSS_PACKAGE_FIXTURE,
                "src/dovo/engine/executors/fixture.py",
                [
                    "src/dovo/engine/executors/fixture.py:3: catch_failure operation 'catalog.load_definition' "
                    "is not 'engine.<entrypoint>'"
                ],
                id="cross-package",
            ),
            pytest.param(
                _SINGULAR_FIXTURE,
                "src/dovo/core/agents/fixture.py",
                [
                    "src/dovo/core/agents/fixture.py:3: catch_failure operation 'agent.invoke' is not 'agents.<entrypoint>'"
                ],
                id="singular-package",
            ),
            pytest.param(
                _REPEATED_FIXTURE,
                "src/dovo/engine/services/fixture.py",
                [
                    "src/dovo/engine/services/fixture.py:3: catch_failure operation 'engine.resume' repeats in this module",
                    "src/dovo/engine/services/fixture.py:5: catch_failure operation 'engine.resume' repeats in this module",
                ],
                id="repeated",
            ),
            pytest.param(
                _ATTRIBUTE_FIXTURE,
                "src/dovo/engine/fixture.py",
                ["src/dovo/engine/fixture.py:3: catch_failure operation 'engine.Run' is not 'engine.<entrypoint>'"],
                id="attribute-call",
            ),
            pytest.param(
                _RECORD_FIXTURE,
                "src/dovo/core/sessions/services/fixture.py",
                [
                    "src/dovo/core/sessions/services/fixture.py:3: operation 'session.reconcile' does not start with "
                    "'sessions.'",
                    "src/dovo/core/sessions/services/fixture.py:4: operation 'engine.persist' does not start with "
                    "'sessions.'",
                    "src/dovo/core/sessions/services/fixture.py:5: operation 'engine.persist' does not start with "
                    "'sessions.'",
                ],
                id="record-exception-package",
            ),
            pytest.param(
                _KEYWORD_CATCH_FIXTURE,
                "src/dovo/engine/services/fixture.py",
                [
                    "src/dovo/engine/services/fixture.py:3: catch_failure operation 'catalog.load_definition' "
                    "is not 'engine.<entrypoint>'",
                    "src/dovo/engine/services/fixture.py:3: catch_failure operation 'catalog.load_definition' "
                    "repeats in this module",
                    "src/dovo/engine/services/fixture.py:5: catch_failure operation 'catalog.load_definition' "
                    "is not 'engine.<entrypoint>'",
                    "src/dovo/engine/services/fixture.py:5: catch_failure operation 'catalog.load_definition' "
                    "repeats in this module",
                ],
                id="keyword-catch",
            ),
            pytest.param(
                _HELPER_FIXTURE,
                "src/dovo/core/worktree/services/fixture.py",
                [
                    "src/dovo/core/worktree/services/fixture.py:3: operation 'worktrees.cleanup' does not start with "
                    "'worktree.'",
                    "src/dovo/core/worktree/services/fixture.py:4: operation 'git.diff' does not start with "
                    "'worktree.'",
                ],
                id="recording-helper",
            ),
            pytest.param(
                _UNRELATED_KEYWORD_FIXTURE, "src/dovo/core/db/fixture.py", [], id="unrelated-operation-keyword"
            ),
            pytest.param(
                _CLI_FIXTURE,
                "src/dovo/cli/session/commands/fixture.py",
                ["src/dovo/cli/session/commands/fixture.py:4: operation 'session.run' does not start with 'cli.'"],
                id="cli-command-group",
            ),
            pytest.param(_COMPLIANT_FIXTURE, "src/dovo/engine/services/fixture.py", [], id="compliant"),
        ],
    )
    def test_rule_flags_only_misnamed_operations(self, source: str, rel_path: str, expected: list[str]) -> None:
        """Return expected from _violations(source, rel_path)."""
        assert _violations(source, rel_path) == expected
