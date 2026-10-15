"""Display form of a progress target shared by every provider's progress mapper."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from dovo.core.agents.models import AgentProgressKind

_PATH_KINDS: Final[frozenset[AgentProgressKind]] = frozenset({AgentProgressKind.READ, AgentProgressKind.EDIT})


def display_progress_target(kind: AgentProgressKind, raw_target: str, worktree_path: Path) -> str:
    """Make a path relative to the worktree or collapse command whitespace, per kind."""
    if kind not in _PATH_KINDS:
        return " ".join(raw_target.split())

    try:
        return str(Path(raw_target).relative_to(worktree_path))
    except ValueError:
        return raw_target
