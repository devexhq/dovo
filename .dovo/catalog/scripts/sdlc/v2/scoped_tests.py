#!/usr/bin/env python3
"""Names the tests the issue-code-v2 blueprint's code loop runs after each phase.

A phase's changes are the files that differ from HEAD in the working tree,
untracked files included, outside .agentic/ and .dovo/run. Each changed file
maps to test paths by the repository's layout:

- a test file under tests/ (`test_*.py`) runs itself;
- any other file under tests/, such as a conftest.py or a harness module, runs
  its directory;
- a module under src/dovo/<layer>/<pkg> runs tests/<layer>/<pkg> when that
  directory exists, else tests/<layer>/test_<pkg>.py when that file exists;
- a file under .dovo/catalog/ runs tests/sdlc.

tests/lint runs whenever anything changed, since its checks span the whole
tree. Paths that do not exist are dropped, and a path inside another listed
directory is folded into it. The paths are printed on one line, separated by
spaces and sorted, for `dovo run tests --path`; nothing is printed when no
file changed. The mapping is a fast first check, not coverage of every caller:
the full gate runs once after the code loop.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

IGNORED_PREFIXES = (".agentic/", ".dovo/run/")
LINT_TESTS = "tests/lint"
CATALOG_TESTS = "tests/sdlc"


def changed_files() -> list[str]:
    """Return the paths that differ from HEAD in the working tree, untracked files included."""
    tracked = _git("diff", "--name-only", "--no-renames", "HEAD")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    return [path for path in [*tracked, *untracked] if not path.startswith(IGNORED_PREFIXES)]


def _git(*args: str) -> list[str]:
    """Return the non-empty lines git prints for args."""
    output = subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout
    return [line for line in output.splitlines() if line]


def _candidates(path: str) -> list[str]:
    """Return the test paths, in order of preference, that cover the changed file path."""
    parts = PurePosixPath(path).parts
    if parts[0] == "tests" and len(parts) > 1:
        if parts[-1].startswith("test_") and parts[-1].endswith(".py"):
            return [path]
        return [str(PurePosixPath(*parts[:-1]))]
    if parts[:2] == ("src", "dovo") and len(parts) > 3:
        layer, package = parts[2], PurePosixPath(parts[3]).stem
        return [f"tests/{layer}/{package}", f"tests/{layer}/test_{package}.py"]
    if parts[:2] == (".dovo", "catalog"):
        return [CATALOG_TESTS]
    return []


def scoped_paths(changed: list[str], root: Path) -> list[str]:
    """Return the sorted test paths under root that cover changed, with tests/lint whenever changed is not empty."""
    if not changed:
        return []
    paths = {LINT_TESTS}
    for path in changed:
        existing = next((c for c in _candidates(path) if c != "tests" and (root / c).exists()), None)
        if existing is not None:
            paths.add(existing)
    return sorted(p for p in paths if not any(p.startswith(f"{other}/") for other in paths))


def main() -> int:
    """Print the test paths that cover the working tree's changes."""
    paths = scoped_paths(changed_files(), Path.cwd())
    if paths:
        sys.stdout.write(" ".join(paths) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
