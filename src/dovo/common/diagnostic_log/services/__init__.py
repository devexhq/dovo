"""Diagnostic log services: the bound CLI command, the exception recorder and its message hint."""

from .command import bind_cli_command, current_cli_command, reset_cli_command
from .record import RETAINED_LOG_DAYS, append_diagnostic_hint, prune_log_days, record_exception

__all__ = [
    "RETAINED_LOG_DAYS",
    "append_diagnostic_hint",
    "bind_cli_command",
    "current_cli_command",
    "prune_log_days",
    "record_exception",
    "reset_cli_command",
]
