"""Low-level Git execution and unified-diff validation package."""

from .exceptions import GitError, MalformedDiffHeader, git_command_error
from .models import GitProcessOutput, GitWorktreeEntry, WorkTreeDiff
from .patch import GitDiffParser, validate_patch_text
from .runner import GitRunner, parse_worktree_porcelain
from .tree_diff import diff_work_tree

__all__ = [
    "GitDiffParser",
    "GitError",
    "GitProcessOutput",
    "GitRunner",
    "GitWorktreeEntry",
    "MalformedDiffHeader",
    "WorkTreeDiff",
    "diff_work_tree",
    "git_command_error",
    "parse_worktree_porcelain",
    "validate_patch_text",
]
