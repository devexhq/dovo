"""The git package's domain error, its fixed-code subclasses for git execution, and the diff-header parse exception."""

from __future__ import annotations

from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import DomainError


class GitError(DomainError):
    """Business failure running git or validating a unified diff."""


class GitPlumbingTimeoutError(GitError):
    """Raised when an internal git plumbing subprocess exceeds its timeout."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.GIT_TIMEOUT, message)


class GitNotFoundError(GitError):
    """Raised when the git executable is not available on PATH."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.GIT_BINARY_MISSING, message)


class GitCommandError(GitError):
    """Raised when git returns a non-zero exit code."""

    def __init__(
        self,
        cmd: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        message = stderr.strip() or stdout.strip() or f"git command failed with exit code {returncode}"
        super().__init__(ErrorCode.GIT_FAILED, f"Git execution failed ('git {' '.join(cmd)}'): {message}")


class MalformedDiffHeader(Exception):
    """Raised when a ``diff --git`` header cannot be parsed."""
