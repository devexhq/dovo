"""Diagnostic check result builder and DiagnosticCheck double for Dovo CLI test suite."""

from __future__ import annotations

from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckResult,
    DiagnosticsContext,
)


def diagnostic_result(**overrides: object) -> DiagnosticCheckResult:
    """Build a passing DiagnosticCheckResult for 'test.check', with any field replaced by overrides."""
    fields: dict[str, object] = {
        "check_id": "test.check",
        "name": "Test Check",
        "category": CheckCategory.GIT,
        "status": CheckStatus.OK,
        "message": "Check passed.",
    }
    return DiagnosticCheckResult.model_validate(fields | overrides)


class StubCheck(DiagnosticCheck):
    """DiagnosticCheck that records its context and returns a result with its own identity and duration_seconds, or raises crash."""

    def __init__(
        self,
        check_id: str = "test.check",
        *,
        name: str = "Test Check",
        category: CheckCategory = CheckCategory.GIT,
        status: CheckStatus = CheckStatus.OK,
        crash: Exception | None = None,
        duration_seconds: float = 0.0,
    ) -> None:
        self.check_id = check_id
        self.name = name
        self.category = category
        self.status = status
        self.crash = crash
        self.duration_seconds = duration_seconds
        self.executed = False
        self.captured_context: DiagnosticsContext | None = None

    def execute(self, context: DiagnosticsContext) -> DiagnosticCheckResult:
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
        )
