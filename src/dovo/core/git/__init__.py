"""Low-level Git execution and unified-diff validation package."""

from .exceptions import (
    GitCommandError,
    GitError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
    MalformedDiffHeader,
)
from .models import GitWorktreeEntry, PatchApplyResult, PatchApplyStatus
from .patch import GitDiffParser, validate_patch_text
from .runner import GitRunner, parse_worktree_porcelain

__all__ = [
    "GitCommandError",
    "GitDiffParser",
    "GitError",
    "GitNotFoundError",
    "GitPlumbingTimeoutError",
    "GitRunner",
    "GitWorktreeEntry",
    "MalformedDiffHeader",
    "PatchApplyResult",
    "PatchApplyStatus",
    "parse_worktree_porcelain",
    "validate_patch_text",
]
