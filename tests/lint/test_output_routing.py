"""Tier 4 invariant: no direct terminal output or interactive prompt outside the CLI dispatcher."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

DISPATCHER_PATH: Final[Path] = SRC_ROOT / "cli" / "ui" / "dispatcher.py"

# typer and click functions that write to stdout: echo directly, and prompts the last character of their question.
BANNED_OUTPUT_FUNCTIONS: Final[frozenset[str]] = frozenset({"echo", "secho", "confirm", "prompt"})

# Files allowed to call builtin input(), whose prompt goes to stdout: each reads only under terminal output.
INPUT_ALLOWED_PATHS: Final[frozenset[Path]] = frozenset({SRC_ROOT / "cli" / "run" / "prompter.py"})


def _is_banned_echo_attribute(func: ast.AST) -> bool:
    """Check if AST node is a typer or click echo, secho, confirm or prompt."""
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in BANNED_OUTPUT_FUNCTIONS:
        return False
    if not isinstance(func.value, ast.Name):
        return False
    return func.value.id in ("typer", "click")


def _check_call_for_banned_output(node: ast.Call, rel_path: Path, allow_input: bool) -> str | None:
    """Inspect an ast.Call node for banned direct output functions.

    Args:
        node: AST Call node.
        rel_path: File path relative to repo root.
        allow_input: Whether the file may call builtin input().

    Returns:
        Violation string if banned call detected, None otherwise.
    """
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        return f"{rel_path}:{node.lineno}: Banned direct terminal output call 'print'"

    if isinstance(node.func, ast.Name) and node.func.id == "input" and not allow_input:
        return f"{rel_path}:{node.lineno}: Banned interactive prompt 'input'; ask through the dispatcher's confirm"

    if _is_banned_echo_attribute(node.func):
        attr_node = node.func
        assert isinstance(attr_node, ast.Attribute)
        assert isinstance(attr_node.value, ast.Name)
        return f"{rel_path}:{node.lineno}: Banned direct terminal output call '{attr_node.value.id}.{attr_node.attr}'"

    return None


def _check_import_from_output(node: ast.ImportFrom, rel_path: Path) -> list[str]:
    """Check if ImportFrom node imports echo, secho, confirm or prompt from typer or click."""
    if node.module not in ("typer", "click"):
        return []
    violations: list[str] = []
    for alias in node.names:
        if alias.name in BANNED_OUTPUT_FUNCTIONS:
            violations.append(
                f"{rel_path}:{node.lineno}: Banned direct terminal output import '{alias.name}' from '{node.module}'"
            )
    return violations


def _scan_file_for_terminal_output(file_path: Path) -> list[str]:
    """Scan a Python file for banned direct console output.

    Args:
        file_path: Path to the Python file.

    Returns:
        List of violation strings with file path and line number.
    """
    allow_input = file_path.resolve() in {path.resolve() for path in INPUT_ALLOWED_PATHS}
    return _scan_source(file_path.read_text(encoding="utf-8"), file_path.relative_to(REPO_ROOT), allow_input)


def _scan_source(source: str, rel_path: Path, allow_input: bool) -> list[str]:
    """Scan Python source for banned direct console output and prompts.

    Args:
        source: Python source text.
        rel_path: File path relative to repo root, used in violation strings.
        allow_input: Whether the source may call builtin input().

    Returns:
        List of violation strings with file path and line number.
    """
    tree = ast.parse(source, filename=str(rel_path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            violations.extend(_check_import_from_output(node, rel_path))
        elif isinstance(node, ast.Call):
            violation = _check_call_for_banned_output(node, rel_path, allow_input)
            if violation:
                violations.append(violation)

    return violations


class OutputRoutingTests:
    """Tier 4 output-routing invariant tests."""

    def test_zero_direct_terminal_output_outside_dispatcher(self) -> None:
        all_files = collect_python_files(SRC_ROOT)
        files = [p for p in all_files if p.resolve() != DISPATCHER_PATH.resolve()]
        violations: list[str] = []
        for file_path in files:
            violations.extend(_scan_file_for_terminal_output(file_path))

        assert not violations, "Found direct terminal output or prompt calls outside dispatcher.py:\n" + "\n".join(
            violations
        )

    @pytest.mark.parametrize(
        ("source", "violation"),
        [
            pytest.param(
                "typer.confirm('Delete?', err=True)\n",
                "Banned direct terminal output call 'typer.confirm'",
                id="typer-confirm",
            ),
            pytest.param(
                "click.prompt('Name')\n", "Banned direct terminal output call 'click.prompt'", id="click-prompt"
            ),
            pytest.param(
                "from typer import confirm\n",
                "Banned direct terminal output import 'confirm' from 'typer'",
                id="import-confirm",
            ),
            pytest.param(
                "input('Name: ')\n",
                "Banned interactive prompt 'input'; ask through the dispatcher's confirm",
                id="input",
            ),
        ],
    )
    def test_interactive_prompt_outside_dispatcher_is_a_violation(self, source: str, violation: str) -> None:
        """Report a typer or click confirm or prompt, even with err=True, and builtin input(), all of which write to stdout."""
        violations = _scan_source(source, Path("src/dovo/cli/example.py"), allow_input=False)

        assert violations == [f"src/dovo/cli/example.py:1: {violation}"]
