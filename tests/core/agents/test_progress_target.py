"""Contract tests for the shared progress target display form."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.agents import AgentProgressKind
from dovo.core.agents.progress_target import display_progress_target

_WORKTREE = Path("/work")


class DisplayProgressTargetTests:
    @pytest.mark.parametrize(
        ("kind", "raw_target", "expected"),
        [
            pytest.param(AgentProgressKind.READ, "/work/src/x.py", "src/x.py", id="read-inside-worktree"),
            pytest.param(AgentProgressKind.EDIT, "/work/notes.txt", "notes.txt", id="edit-inside-worktree"),
            pytest.param(AgentProgressKind.READ, "/elsewhere/x.py", "/elsewhere/x.py", id="outside-worktree-absolute"),
            pytest.param(AgentProgressKind.EDIT, "src/x.py", "src/x.py", id="relative-path-unchanged"),
            pytest.param(AgentProgressKind.SHELL, "ls\n  -la", "ls -la", id="shell-collapsed"),
            pytest.param(AgentProgressKind.SEARCH, "foo\t  bar", "foo bar", id="search-collapsed"),
        ],
    )
    def test_target_is_displayed_per_kind(self, kind: AgentProgressKind, raw_target: str, expected: str) -> None:
        """Return the worktree-relative path for read/edit inside /work, else the input path; collapsed text."""
        assert display_progress_target(kind, raw_target, _WORKTREE) == expected
