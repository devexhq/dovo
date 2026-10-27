"""The git package's domain error, the factory for a failed git command's error, and the diff-header parse exception."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError


class GitError(DomainError):
    """Business failure running git or validating a unified diff."""


def git_command_error(cmd: Sequence[str], returncode: int, stdout: str, stderr: str) -> GitError:
    """Return the GIT_FAILED error for cmd exiting returncode, naming stderr, else stdout, else the exit code."""
    detail = stderr.strip() or stdout.strip() or f"git command failed with exit code {returncode}"
    return GitError(ErrorCode.GIT_FAILED, f"Git execution failed ('git {' '.join(cmd)}'): {detail}")


class MalformedDiffHeader(Exception):
    """Raised when a ``diff --git`` header cannot be parsed."""
