"""Local diagnostic log: caught exceptions recorded as redacted JSON files under <DOVO_HOME>/logs/."""

from .models import DiagnosticLevel, DiagnosticRecord
from .services import (
    append_diagnostic_hint,
    bind_cli_command,
    diagnostic_hint_line,
    record_exception,
    reset_cli_command,
)

__all__ = [
    "DiagnosticLevel",
    "DiagnosticRecord",
    "append_diagnostic_hint",
    "bind_cli_command",
    "diagnostic_hint_line",
    "record_exception",
    "reset_cli_command",
]
