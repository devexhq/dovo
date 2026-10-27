"""Shared exceptions: catalog definition errors, the DomainError base, Failure and its data, and catch_failure for entrypoints."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypedDict

from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord, error_diagnostics, record_exception
from dovo.common.error_codes import ErrorCode
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation

_ContextValue = str | int | float | bool | None


class DefinitionError(Exception):
    """Base class for domain definition errors."""


class DefinitionNotFoundError(DefinitionError):
    """Raised when a definition file or identifier cannot be found."""


class DefinitionLoadError(DefinitionError):
    """Raised when definition YAML syntax is invalid or cannot be parsed/loaded."""


class DefinitionValidationError(DefinitionError):
    """Raised when definition schema or model validation fails."""


@dataclass(frozen=True, slots=True)
class FailureData:
    """Base for the typed extra data a package attaches to a failure, such as an apply conflict's paths."""


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
