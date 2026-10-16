"""Invariant: src/dovo writes files only through common/filesystem, so state on disk is always written atomically."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

FILESYSTEM_ROOT: Final[Path] = SRC_ROOT / "common" / "filesystem"
WRITE_METHODS: Final[frozenset[str]] = frozenset({"write_text", "write_bytes"})

# Direct writes of files that are not .dovo state: (file, call). Entries are only ever removed, never added.
WRITE_EXCEPTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # Scratch buffer handed to the user's editor inside a TemporaryDirectory.
        ("src/dovo/cli/run/prompter.py", ".write_text"),
        # Probe file that only tests whether a directory is writable.
        ("src/dovo/core/diagnostics/checks/filesystem_writable.py", ".write_text"),
        # Attempt log streamed while a subprocess runs, and the step output file the step itself fills.
        ("src/dovo/engine/executors/step_executor.py", "open"),
        ("src/dovo/engine/executors/step_executor.py", ".write_text"),
        # Append-only session log, written line by line while a session runs.
        ("src/dovo/engine/executors/session_log.py", ".open"),
    }
)


def _write_mode(node: ast.Call, mode_index: int) -> str | None:
    """Return the literal mode of an open call, or None when it is not a literal.

    `mode_index` is the positional slot of the mode: 1 for `open(path, mode)`, 0 for `path.open(mode)`.
    """
    positional = node.args[mode_index] if len(node.args) > mode_index else None
    mode = positional or next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return mode.value
    return None


def _opens_for_writing(node: ast.Call, mode_index: int) -> bool:
    """Return whether an open call's literal mode writes, appends, creates or updates."""
    return bool(set(_write_mode(node, mode_index) or "") & set("wax+"))


def _direct_writes(file_path: Path) -> set[tuple[str, str]]:
    """Return (file, call) for each direct file write in one module."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = str(file_path.relative_to(REPO_ROOT))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_METHODS:
            found.add((rel_path, f".{node.func.attr}"))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open" and _opens_for_writing(node, 0):
            found.add((rel_path, ".open"))
        elif isinstance(node.func, ast.Name) and node.func.id == "open" and _opens_for_writing(node, 1):
            found.add((rel_path, "open"))
    return found


class DirectFileWriteTests:
    def test_src_writes_files_only_through_common_filesystem(self) -> None:
        files = [path for path in collect_python_files(SRC_ROOT) if not path.is_relative_to(FILESYSTEM_ROOT)]
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        violations = sorted(write for path in files for write in _direct_writes(path) - WRITE_EXCEPTIONS)

        assert violations == [], "write through Filesystem.atomic_write_* instead"

    @pytest.mark.parametrize(
        "exception", [pytest.param(item, id=f"{item[0]}{item[1]}") for item in sorted(WRITE_EXCEPTIONS)]
    )
    def test_write_exception_still_occurs(self, exception: tuple[str, str]) -> None:
        """Require every allowlisted write to still exist, so a fixed one is removed from the list."""
        assert exception in _direct_writes(REPO_ROOT / exception[0]), "delete it from WRITE_EXCEPTIONS"
