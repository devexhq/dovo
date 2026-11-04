"""Shared exceptions: the DomainError base, Failure and its data, catch_failure, and escaped-failure headers."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation

_ContextValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class FailureData:
    """Base for the typed extra data a package attaches to a failure, such as an apply conflict's paths."""


@dataclass(frozen=True, slots=True)
class RecordedDiagnostics(FailureData):
    """The diagnostic records written where the failure arose, carried to the handler that reports it."""

    records: tuple[DiagnosticRecord, ...]


class DomainError(Exception):
    """Base for each package's one business-failure exception."""

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        remediations: Sequence[Remediation] = (),
        *,
        data: FailureData | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.remediations: tuple[Remediation, ...] = tuple(remediations)
        self.data = data


class FailureFields(TypedDict):
    """The result header fields a failure fills, spread into a result constructor."""

    error_code: ErrorCode
    errors: list[str]
    remediations: list[Remediation]
    diagnostics: list[DiagnosticRecord]


@dataclass(frozen=True, slots=True)
class Failure:
    """A failure caught at an entrypoint, ready to copy into its result."""

    error_code: ErrorCode
    message: str
    remediations: tuple[Remediation, ...]
    diagnostic: DiagnosticRecord | None
    data: FailureData | None = None

    def result_fields(self, *, prior_diagnostics: Sequence[DiagnosticRecord] = ()) -> FailureFields:
        """Return the code, the message as errors, the remediations, and prior_diagnostics followed by the error-level record."""
        return FailureFields(
            error_code=self.error_code,
            errors=[self.message],
            remediations=list(self.remediations),
            diagnostics=[*prior_diagnostics, *error_diagnostics(self.diagnostic)],
        )


@dataclass(slots=True)
class CaughtFailure:
    """Set by catch_failure when its block raised; None while the block succeeds."""

    failure: Failure | None = None


@contextmanager
def catch_failure(operation: str, **context: _ContextValue) -> Generator[CaughtFailure]:
    """Turn a DomainError or unexpected exception raised in the block into caught.failure, recording unexpected ones and OSError causes."""
    caught = CaughtFailure()
    try:
        yield caught
    except LockTimeoutError:
        raise
    except DomainError as error:
        caught.failure = _domain_failure(error, operation, context)
    except Exception as error:
        caught.failure = _unexpected_failure(error, operation, context)


def _domain_failure(error: DomainError, operation: str, context: Mapping[str, _ContextValue]) -> Failure:
    """Copy error into a Failure, recording its OSError cause at error level when it has one."""
    cause = error.__cause__
    diagnostic = (
        record_exception(cause, level=DiagnosticLevel.ERROR, operation=operation, context=context)
        if isinstance(cause, OSError)
        else None
    )
    return Failure(
        error_code=error.error_code,
        message=error.message,
        remediations=error.remediations,
        diagnostic=diagnostic,
        data=error.data,
    )


def _unexpected_failure(error: Exception, operation: str, context: Mapping[str, _ContextValue]) -> Failure:
    """Record error at error level and return an INTERNAL_ERROR Failure naming operation and str(error)."""
    return Failure(
        error_code=ErrorCode.INTERNAL_ERROR,
        message=f"Unexpected error during {operation}: {error}",
        remediations=(),
        diagnostic=record_exception(error, level=DiagnosticLevel.ERROR, operation=operation, context=context),
    )


class WorkspaceError(DomainError):
    """Business failure resolving the workspace a command runs in."""


INIT_REMEDIATION: Final[Remediation] = Remediation(message="Initialize the workspace.", command="dovo init")
REPLACE_IDENTITY_REMEDIATION: Final[Remediation] = Remediation(
    message="Run 'dovo init --id <project-id> --force' to replace the unusable identity.",
    command="dovo init --id <project-id> --force",
)
LOCK_TIMEOUT_REMEDIATION: Final[Remediation] = Remediation(
    message="Wait for the other dovo process to release the workspace lock, then retry."
)


def workspace_not_initialized_error(
    identity_path: Path, *, identity_present: bool, diagnostic: DiagnosticRecord | None
) -> WorkspaceError:
    """Return WORKSPACE_NOT_INITIALIZED naming identity_path, carrying diagnostic as RecordedDiagnostics.

    It has the init remediation, then the --force one when the identity file is present.
    """
    remediations = [INIT_REMEDIATION, REPLACE_IDENTITY_REMEDIATION] if identity_present else [INIT_REMEDIATION]
    return WorkspaceError(
        ErrorCode.WORKSPACE_NOT_INITIALIZED,
        f"Workspace is not initialized: no valid project identity at '{identity_path}'.",
        remediations,
        data=RecordedDiagnostics(tuple(error_diagnostics(diagnostic))),
    )


def _notes(error: BaseException) -> list[str]:
    """Return each note added to error, in order."""
    return list(getattr(error, "__notes__", []))


def escaped_failure_fields(error: DomainError | LockTimeoutError) -> FailureFields:
    """Return the failed-result header of a failure no command result carries.

    A DomainError gives its code, message, remediations and carried RecordedDiagnostics records; a lock timeout gives
    WORKSPACE_LOCK_TIMEOUT, its message and the wait remediation. errors ends with one entry per note on error.
    """
    if isinstance(error, LockTimeoutError):
        return FailureFields(
            error_code=ErrorCode.WORKSPACE_LOCK_TIMEOUT,
            errors=[str(error), *_notes(error)],
            remediations=[LOCK_TIMEOUT_REMEDIATION],
            diagnostics=[],
        )
    records = error.data.records if isinstance(error.data, RecordedDiagnostics) else ()
    return FailureFields(
        error_code=error.error_code,
        errors=[error.message, *_notes(error)],
        remediations=list(error.remediations),
        diagnostics=list(records),
    )


def fatal_failure_fields(error: Exception, record: DiagnosticRecord | None) -> FailureFields:
    """Return the INTERNAL_ERROR header of an unexpected exception that reached the top-level handler.

    errors holds the fatal message, then one entry per note on error; diagnostics holds record when it is error-level.
    """
    return FailureFields(
        error_code=ErrorCode.INTERNAL_ERROR,
        errors=[f"A fatal unexpected error occurred. Details: {error}", *_notes(error)],
        remediations=[],
        diagnostics=error_diagnostics(record),
    )
