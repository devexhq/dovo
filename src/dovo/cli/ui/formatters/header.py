"""The header every command-result JSON payload starts with.

A command result's view subclasses ResultHeaderView and declares only its own fields; Pydantic puts parent fields
first, so the header always leads the payload in the order of HEADER_FIELDS.
"""

from __future__ import annotations

from typing import Final, Self, TypedDict

from pydantic import BaseModel, Field, model_validator

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import ERROR_CODE_MISMATCH_MESSAGE, BaseResult, Remediation

HEADER_FIELDS: Final[tuple[str, ...]] = ("ok", "error_code", "errors", "warnings", "remediations", "diagnostics")
OK_MISMATCH_MESSAGE: Final[str] = "ok must be true exactly when error_code is None"


class ResultHeaderFields(TypedDict):
    """The header of a command result, as view keywords."""

    ok: bool
    error_code: ErrorCode | None
    errors: list[str]
    warnings: list[str]
    remediations: list[Remediation]
    diagnostics: list[DiagnosticRecord]


class ResultHeaderView(BaseModel):
    """The header every command-result payload starts with; a command view subclasses it and adds its own fields."""

    model_config = {"extra": "forbid", "strict": True}

    ok: bool
    error_code: ErrorCode | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_header_agreement(self) -> Self:
        """Refuse a header whose ok or errors disagree with error_code."""
        if self.ok != (self.error_code is None):
            raise ValueError(OK_MISMATCH_MESSAGE)
        if bool(self.errors) != (self.error_code is not None):
            raise ValueError(ERROR_CODE_MISMATCH_MESSAGE)
        return self


def header_fields(result: BaseResult) -> ResultHeaderFields:
    """Copy result's header into view keywords, with ok taken from error_code.

    Args:
        result: The command result whose header the view carries.

    Returns:
        The six header fields, ready to unpack into a ResultHeaderView subclass.
    """
    return ResultHeaderFields(
        ok=result.ok,
        error_code=result.error_code,
        errors=list(result.errors),
        warnings=list(result.warnings),
        remediations=list(result.remediations),
        diagnostics=list(result.diagnostics),
    )
