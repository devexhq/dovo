"""Record unexpected worktree failures in the diagnostic log and append the hint to the reported message."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from dovo.common.diagnostic_log import DiagnosticLevel, append_diagnostic_hint, record_exception
from dovo.core.git.exceptions import GitError, GitNotFoundError
from dovo.core.worktree.exceptions import WorktreeError

DiagnosticContext = Mapping[str, str | int | float | bool | None]

_EXPECTED_ERRORS: Final = (GitError, WorktreeError)


def logged_failure(message: str, exc: Exception, *, operation: str, context: DiagnosticContext) -> str:
    """Return message unchanged for a GitError or WorktreeError; else record exc at error level and append the hint."""
    if isinstance(exc, _EXPECTED_ERRORS):
        return message

    return append_diagnostic_hint(
        message, record_exception(exc, level=DiagnosticLevel.ERROR, operation=operation, context=context)
    )


def record_fallback(exc: Exception, *, operation: str, context: DiagnosticContext) -> None:
    """Record exc at debug level for a failure Dovo recovered from, skipping GitNotFoundError."""
    if isinstance(exc, GitNotFoundError):
        return

    record_exception(exc, level=DiagnosticLevel.DEBUG, operation=operation, context=context)
