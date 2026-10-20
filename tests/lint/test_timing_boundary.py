"""Invariant: src/dovo reads clocks only through common/timing, so every elapsed time is measured by Stopwatch."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from tests.lint.astlib import REPO_ROOT, SRC_ROOT, collect_python_files

TIMING_MODULE: Final[Path] = SRC_ROOT / "common" / "timing.py"
CLOCK_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {"monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "time", "time_ns"}
)

# Deadline polling not yet moved onto common/timing. Entries are only ever removed, never added.
TIMING_ALLOWLIST: Final[frozenset[str]] = frozenset({"src/dovo/common/lock.py", "src/dovo/common/process.py"})

_CLOCK_READ_FIXTURE: Final[str] = """
import time


class Poller:
    def wait(self) -> float:
        return time.monotonic()
"""

_FROM_IMPORT_FIXTURE: Final[str] = """
from time import perf_counter
"""

_NS_CLOCK_FIXTURE: Final[str] = """
import time
started = time.monotonic_ns()
"""

_ALIASED_IMPORT_FIXTURE: Final[str] = """
import time as clock
started = clock.monotonic()
"""

_REFERENCE_FIXTURE: Final[str] = """
import time
from dataclasses import dataclass, field


@dataclass
class Span:
    started: float = field(default_factory=time.monotonic)
"""


def _time_module_names(tree: ast.Module) -> set[str]:
    """Return the local names `import time` binds in tree, including `import time as <alias>`."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "time"
    }


def _clock_reads(source: str, rel_path: str) -> list[str]:
    """Return '<rel_path>:<line>: reads time.<name>' for each `time.<clock>` reference, called or not, and `from time import <clock>`."""
    tree = ast.parse(source, filename=rel_path)
    time_names = _time_module_names(tree)
    reads: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "time":
            reads.extend(
                f"{rel_path}:{node.lineno}: reads time.{alias.name}"
                for alias in node.names
                if alias.name in CLOCK_FUNCTIONS
            )
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in time_names
            and node.attr in CLOCK_FUNCTIONS
        ):
            reads.append(f"{rel_path}:{node.lineno}: reads time.{node.attr}")
    return reads


def _module_clock_reads(file_path: Path) -> list[str]:
    """Return the clock reads of one source file, keyed by its repo-relative path."""
    return _clock_reads(file_path.read_text(encoding="utf-8"), str(file_path.relative_to(REPO_ROOT)))


class TimingBoundaryTests:
    def test_src_reads_clocks_only_through_common_timing(self) -> None:
        """Report no clock read in src/dovo outside common/timing.py and the allowlist."""
        files = [
            path
            for path in collect_python_files(SRC_ROOT)
            if path != TIMING_MODULE and str(path.relative_to(REPO_ROOT)) not in TIMING_ALLOWLIST
        ]
        assert files, f"{SRC_ROOT} contains no Python files, so the rule would pass vacuously"

        violations = sorted(read for path in files for read in _module_clock_reads(path))

        assert violations == [], "read time through dovo.common.timing.Stopwatch instead"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(_CLOCK_READ_FIXTURE, "src/dovo/fixture.py:7: reads time.monotonic", id="attribute-call"),
            pytest.param(_FROM_IMPORT_FIXTURE, "src/dovo/fixture.py:2: reads time.perf_counter", id="from-import"),
            pytest.param(_NS_CLOCK_FIXTURE, "src/dovo/fixture.py:3: reads time.monotonic_ns", id="ns-variant"),
            pytest.param(_ALIASED_IMPORT_FIXTURE, "src/dovo/fixture.py:3: reads time.monotonic", id="aliased-import"),
            pytest.param(_REFERENCE_FIXTURE, "src/dovo/fixture.py:8: reads time.monotonic", id="bare-reference"),
        ],
    )
    def test_rule_flags_a_clock_read(self, source: str, expected: str) -> None:
        """Return exactly [expected] from _clock_reads(source, 'src/dovo/fixture.py')."""
        assert _clock_reads(source, "src/dovo/fixture.py") == [expected]

    @pytest.mark.parametrize("rel_path", sorted(TIMING_ALLOWLIST))
    def test_allowlisted_module_still_reads_a_clock(self, rel_path: str) -> None:
        """Require each allowlisted module to still read a clock, so a converted module leaves the allowlist."""
        assert _module_clock_reads(REPO_ROOT / rel_path), "delete it from TIMING_ALLOWLIST"
