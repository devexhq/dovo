"""Invariant: src/dovo writes files only through common/filesystem, so state on disk is always written atomically."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

FILESYSTEM_ROOT: Final[Path] = SRC_ROOT / "common" / "filesystem"
WRITE_METHODS: Final[frozenset[str]] = frozenset({"write_text", "write_bytes"})

# Direct writes of files that are not .dovo state: (file, call) -> how many such calls the file makes.
# Counts are matched exactly, so a new write in a listed file fails too. Entries only shrink, never grow.
WRITE_EXCEPTIONS: Final[dict[tuple[str, str], int]] = {
    # Scratch buffer handed to the user's editor inside a TemporaryDirectory.
    ("src/dovo/cli/run/prompter.py", ".write_text"): 1,
    # Not a file write: UiDispatcher.write_text sends the plain patch to stdout for `dovo diff --patch`.
    ("src/dovo/cli/session/commands/diff.py", ".write_text"): 1,
    # Probe file that only tests whether a directory is writable.
    ("src/dovo/core/diagnostics/checks/filesystem_writable.py", ".write_text"): 1,
    # Attempt log streamed while a subprocess runs, and the step output file the step itself fills.
    ("src/dovo/engine/executors/step_executor.py", "open"): 1,
    ("src/dovo/engine/executors/step_executor.py", ".write_text"): 1,
    # Append-only session log, written line by line while a session runs.
    ("src/dovo/engine/executors/session_log.py", ".open"): 1,
}


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


def _direct_writes(file_path: Path) -> Counter[tuple[str, str]]:
    """Return how many direct file writes one module makes, keyed by (file, call)."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    rel_path = str(file_path.relative_to(REPO_ROOT))
    found: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_METHODS:
            found[rel_path, f".{node.func.attr}"] += 1
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open" and _opens_for_writing(node, 0):
            found[rel_path, ".open"] += 1
        elif isinstance(node.func, ast.Name) and node.func.id == "open" and _opens_for_writing(node, 1):
            found[rel_path, "open"] += 1
    return found


class DirectFileWriteTests:
    def test_src_writes_files_only_through_common_filesystem(self) -> None:
        files = [path for path in collect_python_files(SRC_ROOT) if not path.is_relative_to(FILESYSTEM_ROOT)]
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        allowed = Counter(WRITE_EXCEPTIONS)
        violations = sorted(write for path in files for write in (_direct_writes(path) - allowed).elements())

        assert violations == [], "write through Filesystem.atomic_write_* instead"

    @pytest.mark.parametrize(
        ("exception", "count"),
        [pytest.param(item, count, id=f"{item[0]}{item[1]}") for item, count in sorted(WRITE_EXCEPTIONS.items())],
    )
    def test_write_exception_still_occurs(self, exception: tuple[str, str], count: int) -> None:
        """Require each allowlisted count to match the tree, so a removed write lowers or deletes its entry."""
        assert _direct_writes(REPO_ROOT / exception[0])[exception] == count, "lower or delete it in WRITE_EXCEPTIONS"
