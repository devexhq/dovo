"""Record unexpected engine failures in the diagnostic log and return the hint for the reported message."""

from __future__ import annotations

from collections.abc import Mapping

from dovo.common.diagnostic_log import DiagnosticLevel, append_diagnostic_hint, diagnostic_hint_line, record_exception

DiagnosticContext = Mapping[str, str | int | float | bool | None]


def logged_failure(message: str, exc: BaseException, *, operation: str, context: DiagnosticContext) -> str:
    """Record exc at error level and return message with the 'Details logged' line appended, or unchanged when unrecorded."""
    return append_diagnostic_hint(
        message, record_exception(exc, level=DiagnosticLevel.ERROR, operation=operation, context=context)
    )


def logged_hint(exc: BaseException, *, operation: str, context: DiagnosticContext) -> str | None:
    """Record exc at error level and return the bare 'Details logged' line for a warnings entry, or None when unrecorded."""
    return diagnostic_hint_line(
        record_exception(exc, level=DiagnosticLevel.ERROR, operation=operation, context=context)
    )


def record_fallback(exc: BaseException, *, operation: str, context: DiagnosticContext) -> None:
    """Record exc at debug level for a failure the run carried on from."""
    record_exception(exc, level=DiagnosticLevel.DEBUG, operation=operation, context=context)
