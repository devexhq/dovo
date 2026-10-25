"""Pydantic models for low-level Git operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class GitProcessOutput:
    """Exit code and captured text output of one git subprocess."""

    returncode: int
    stdout: str
    stderr: str


class GitWorktreeEntry(BaseModel):
    """Parsed entry from `git worktree list --porcelain`."""

    model_config = {"extra": "forbid", "strict": True}

    path: Path
    head_sha: str = ""
    branch: str | None = None
    is_bare: bool = False
    is_detached: bool = False
    is_locked: bool = False
    is_prunable: bool = False
    prunable_reason: str | None = None


class WorkTreeDiff(BaseModel):
    """A worktree's working tree compared with a base commit."""

    model_config = {"extra": "forbid", "strict": True}

    diff_text: str = ""
    files: list[str] = Field(default_factory=list)
    stat_text: str = ""
