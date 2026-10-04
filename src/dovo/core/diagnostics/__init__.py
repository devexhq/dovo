"""Diagnostic check registry and execution engine."""

from dovo.core.diagnostics.diagnostics import Diagnostics
from dovo.core.diagnostics.exceptions import CheckRegistrationError, DiagnosticsError
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckResult,
    DiagnosticsContext,
    DiagnosticsReport,
    Remediation,
    RemediationType,
)

__all__ = [
    "CheckCategory",
    "CheckRegistrationError",
    "CheckStatus",
    "DiagnosticCheck",
    "DiagnosticCheckResult",
    "Diagnostics",
    "DiagnosticsContext",
    "DiagnosticsError",
    "DiagnosticsReport",
    "Remediation",
    "RemediationType",
]
