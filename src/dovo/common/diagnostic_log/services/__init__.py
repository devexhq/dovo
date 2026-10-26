"""Diagnostic log services: the bound CLI command, the exception recorder, its message hint and error_diagnostics."""

from .command import bind_cli_command, current_cli_command, reset_cli_command
from .record import (
    RETAINED_LOG_DAYS,
    append_diagnostic_hint,
    diagnostic_hint_line,
    error_diagnostics,
    prune_log_days,
    record_exception,
)

__all__ = [
    "RETAINED_LOG_DAYS",
    "append_diagnostic_hint",
    "bind_cli_command",
    "current_cli_command",
    "diagnostic_hint_line",
    "error_diagnostics",
    "prune_log_days",
    "record_exception",
    "reset_cli_command",
]
