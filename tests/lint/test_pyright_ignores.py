"""Invariant: every `pyright: ignore[...]` in src/dovo and tests carries a trailing reason comment."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, TESTS_ROOT, collect_python_files

IGNORE: Final[re.Pattern[str]] = re.compile(r"#\s*pyright:\s*ignore\[[^\]]+\](?P<rest>.*)$")


def _ignores_without_reason(file_path: Path) -> list[str]:
    """Return `path:line` for each ignore in one file that has no `# reason` after it."""
    rel_path = file_path.relative_to(REPO_ROOT)
    return [
        f"{rel_path}:{number}"
        for number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1)
        if (match := IGNORE.search(line)) and not match.group("rest").strip().lstrip("#").strip()
    ]


class PyrightIgnoreTests:
    def test_every_ignore_names_its_reason(self) -> None:
        files = [*collect_python_files(SRC_ROOT), *collect_python_files(TESTS_ROOT)]
        assert files, "no Python files found, so the rule would pass vacuously"

        violations = [found for path in files for found in _ignores_without_reason(path)]

        assert violations == [], "append `# <why this case is permitted>` after the ignore"
