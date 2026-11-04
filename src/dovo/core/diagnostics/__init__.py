"""Diagnostic check registry and execution engine."""

from dovo.core.diagnostics.diagnostics import Diagnostics
from dovo.core.diagnostics.exceptions import CheckRegistrationError
from dovo.core.diagnostics.models import (
    CheckCategory,
    CheckStatus,
    DiagnosticCheck,
    DiagnosticCheckRecord,
    DiagnosticsContext,
    DiagnosticsReport,
)

__all__ = [
    "CheckCategory",
    "CheckRegistrationError",
    "CheckStatus",
    "DiagnosticCheck",
    "DiagnosticCheckRecord",
    "Diagnostics",
    "DiagnosticsContext",
    "DiagnosticsReport",
]
