"""Pydantic models for low-level Git operations."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


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
