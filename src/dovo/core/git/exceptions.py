"""Exceptions for low-level Git execution."""

from __future__ import annotations


class GitError(RuntimeError):
    """Base exception for Git command execution failures."""


class GitPlumbingTimeoutError(GitError):
    """Raised when an internal git plumbing subprocess exceeds its timeout."""


class GitNotFoundError(GitError):
    """Raised when the git executable is not available on PATH."""


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
        super().__init__(f"Git execution failed ('git {' '.join(cmd)}'): {message}")
