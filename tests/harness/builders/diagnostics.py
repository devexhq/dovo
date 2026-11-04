"""Diagnostic check result builder and DiagnosticCheck double for Dovo CLI test suite."""

from __future__ import annotations

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckRecord,
    DiagnosticsContext,
)


def diagnostic_result(**overrides: object) -> DiagnosticCheckRecord:
    """Build a passing DiagnosticCheckRecord for 'test.check', with any field replaced by overrides.

    A FAILED record given no error_code carries DOCTOR_GIT_REPO_NOT_FOUND, since a failed check must carry one.
    """
    fields: dict[str, object] = {
        "check_id": "test.check",
        "name": "Test Check",
        "category": CheckCategory.GIT,
        "status": CheckStatus.OK,
        "message": "Check passed.",
    }
    if overrides.get("status") == CheckStatus.FAILED:
        fields["error_code"] = ErrorCode.DOCTOR_GIT_REPO_NOT_FOUND
    return DiagnosticCheckRecord.model_validate(fields | overrides)


class StubCheck(DiagnosticCheck):
    """DiagnosticCheck that records its context and returns a result with its own identity, duration_seconds and diagnostics, or raises crash."""

    def __init__(
        self,
        check_id: str = "test.check",
        *,
        name: str = "Test Check",
        category: CheckCategory = CheckCategory.GIT,
        status: CheckStatus = CheckStatus.OK,
        crash: Exception | None = None,
        duration_seconds: float = 0.0,
        diagnostics: list[DiagnosticRecord] | None = None,
    ) -> None:
        self.check_id = check_id
        self.name = name
        self.category = category
        self.status = status
        self.crash = crash
        self.duration_seconds = duration_seconds
        self.diagnostics = list(diagnostics or [])
        self.executed = False
        self.captured_context: DiagnosticsContext | None = None

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckRecord:
        """Record context, then raise crash when set, else return a result carrying this check's status."""
        self.executed = True
        self.captured_context = context
        if self.crash is not None:
            raise self.crash
        return diagnostic_result(
            check_id=self.check_id,
            name=self.name,
            category=self.category,
            status=self.status,
            message=f"Executed {self.check_id}",
            duration_seconds=self.duration_seconds,
            diagnostics=self.diagnostics,
        )
