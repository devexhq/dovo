"""Invariant: no error message built in src/dovo carries a code token such as `(WORKTREE_NOT_FOUND)`.

Scope: the rule reads every string literal and literal f-string part inside an error-message site and flags text
matching `\\([A-Z][A-Z_]{3,}\\)`. An error-message site is any argument of a call to a name ending in `Error`
(`WorktreeError(...)`, `errors.GitError(...)`, `ValueError(...)`), the value of an `errors=` or `error_message=`
keyword, the arguments of `.append` or `.insert` on a name containing `errors`, and the value assigned to such a name.
Warnings are not error messages and are not read. Not detected: a message built in a helper or variable and passed
to the site by name (`_failed(status, code, message)`, `message = f"..."; raise X(message)`), and a token formatted
from a variable (`f"({code})"`).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

ENTRY: Final[str] = "docs/agents/architecture/exceptions.md#no-code-tokens-in-messages"
TOKEN: Final[re.Pattern[str]] = re.compile(r"\([A-Z][A-Z_]{3,}\)")
MESSAGE_KEYWORDS: Final[frozenset[str]] = frozenset({"errors", "error_message"})
LIST_METHODS: Final[frozenset[str]] = frozenset({"append", "insert"})

_RAISE_FIXTURE: Final[str] = """
def load(path):
    raise WorktreeError(ErrorCode.WORKTREE_NOT_FOUND, f"Worktree '{path}' not found (WORKTREE_NOT_FOUND).")
"""

_ERRORS_KEYWORD_FIXTURE: Final[str] = """
def read(path):
    return LoadResult(errors=["\\n".join(["Schema validation failed (CONFIG_SCHEMA_INVALID):", "- detail"])])
"""

_ERRORS_LIST_FIXTURE: Final[str] = """
def audit(errors_out, detail):
    errors = [f"Blocked (AGENT_PERMISSION_BLOCKED): {detail}"]
    errors_out.insert(0, f"Provider error (AGENT_PROVIDER_ERROR): {detail}")
    return errors
"""

_CLEAN_FIXTURE: Final[str] = """
def load(path, detail):
    warnings = [f"'{path}' exceeds 10 (CONFIG_WARN_WORKTREE_LIMIT)."]
    if detail:
        raise WorktreeError(ErrorCode.WORKTREE_NOT_FOUND, f"Worktree '{path}' not found.")
    return LoadResult(errors=[f"Unable to read '{path}': {detail}"], warnings=warnings)
"""


def _callee_name(func: ast.expr) -> str | None:
    """Return the simple name a call's function refers to, or None for any other form."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_errors_name(node: ast.expr) -> bool:
    """Return whether node is a name or attribute whose name contains `errors`."""
    name = _callee_name(node)
    return name is not None and "errors" in name


def _call_message_parts(call: ast.Call) -> list[ast.expr]:
    """Return the expressions of call that carry an error message, or an empty list."""
    name = _callee_name(call.func)
    keyword_values = [keyword.value for keyword in call.keywords if keyword.arg in MESSAGE_KEYWORDS]
    if name is not None and name.endswith("Error"):
        return [*call.args, *(keyword.value for keyword in call.keywords)]
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in LIST_METHODS and _is_errors_name(func.value):
        return [*call.args, *keyword_values]
    return keyword_values


def _message_parts(tree: ast.Module) -> Iterator[ast.expr]:
    """Yield every expression in tree that is, or builds, an error message."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield from _call_message_parts(node)
        elif isinstance(node, ast.Assign) and any(_is_errors_name(target) for target in node.targets):
            yield node.value


def _literal_texts(expr: ast.expr) -> Iterator[ast.Constant]:
    """Yield every string constant inside expr, including the literal parts of f-strings."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def _token_violations(source: str, rel_path: str) -> list[str]:
    """Return '<rel_path>:<line>: <token>' for each code token in an error-message literal, sorted and unique."""
    found = {
        f"{rel_path}:{constant.lineno}: {match.group(0)}"
        for part in _message_parts(ast.parse(source, filename=rel_path))
        for constant in _literal_texts(part)
        for match in TOKEN.finditer(str(constant.value))
    }
    return sorted(found)


class ErrorMessageTokenTests:
    def test_src_error_messages_carry_no_code_token(self) -> None:
        """Report no code token in a string literal at an error-message site in src/dovo."""
        files = collect_python_files(SRC_ROOT)
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        violations = [
            violation
            for path in files
            for violation in _token_violations(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
        ]

        assert violations == [], f"drop the token; error_code is the failure's only classification. See {ENTRY}"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(_RAISE_FIXTURE, ["src/dovo/fixture.py:3: (WORKTREE_NOT_FOUND)"], id="error-call"),
            pytest.param(
                _ERRORS_KEYWORD_FIXTURE, ["src/dovo/fixture.py:3: (CONFIG_SCHEMA_INVALID)"], id="errors-keyword"
            ),
            pytest.param(
                _ERRORS_LIST_FIXTURE,
                ["src/dovo/fixture.py:3: (AGENT_PERMISSION_BLOCKED)", "src/dovo/fixture.py:4: (AGENT_PROVIDER_ERROR)"],
                id="errors-list",
            ),
            pytest.param(_CLEAN_FIXTURE, [], id="clean-and-warnings"),
        ],
    )
    def test_rule_flags_tokens_only_in_error_messages(self, source: str, expected: list[str]) -> None:
        """Return expected from _token_violations(source, 'src/dovo/fixture.py')."""
        assert _token_violations(source, "src/dovo/fixture.py") == expected
