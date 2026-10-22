"""Diagnostic log levels, the event file shape, and the record returned to callers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DiagnosticLevel(StrEnum):
    ERROR = "error"
    DEBUG = "debug"


class DiagnosticRecord(BaseModel):
    """A written diagnostic event: its id, absolute file path and level."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event_id: str
    log_path: Path
    level: DiagnosticLevel


class DiagnosticEvent(BaseModel):
    """The JSON object stored in one event file."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event_id: str
    timestamp: str
    level: DiagnosticLevel
    operation: str
    command: str | None
    context: dict[str, str | int | float | bool | None]
    exception_type: str
    exception_message: str
    formatted_traceback: str
