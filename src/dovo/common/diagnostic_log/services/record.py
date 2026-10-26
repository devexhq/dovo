"""Record caught exceptions as redacted JSON files under <DOVO_HOME>/logs/."""

from __future__ import annotations

import re
import shutil
import traceback
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

from dovo.common.diagnostic_log.models import DiagnosticEvent, DiagnosticLevel, DiagnosticRecord
from dovo.common.diagnostic_log.services.command import current_cli_command
from dovo.common.filesystem.services import atomic_write_json, resolve_global_paths
from dovo.common.redact import SecretRedactor

RETAINED_LOG_DAYS: Final[int] = 14
_DAY_DIRECTORY: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def record_exception(
    exc: BaseException,
    *,
    level: DiagnosticLevel,
    operation: str,
    context: Mapping[str, str | int | float | bool | None] | None = None,
) -> DiagnosticRecord | None:
    """Write one redacted event for exc and prune old days; return None, never raising, when it cannot be written."""
    try:
        record = _write_event(exc, level=level, operation=operation, context=context or {})
    except Exception:
        return None
    if record is None:
        return None

    prune_log_days(record.log_path.parent.parent)
    return record


def _write_event(
    exc: BaseException,
    *,
    level: DiagnosticLevel,
    operation: str,
    context: Mapping[str, str | int | float | bool | None],
) -> DiagnosticRecord | None:
    """Build and atomically write the event file at mode 0600; return None when the id's file already exists."""
    logs_dir = resolve_global_paths().logs_dir
    moment = datetime.now().astimezone()
    event_id = f"exc_{uuid4().hex[:12]}"
    log_path = logs_dir / moment.date().isoformat() / f"{event_id}.json"
    if log_path.exists():
        return None

    redactor = SecretRedactor.from_environment()
    event = DiagnosticEvent(
        event_id=event_id,
        timestamp=moment.isoformat(timespec="seconds"),
        level=level,
        operation=operation,
        command=current_cli_command(),
        context={
            key: redactor.redact_text(value) if isinstance(value, str) else value for key, value in context.items()
        },
        exception_type=type(exc).__name__,
        exception_message=redactor.redact_text(str(exc)),
        formatted_traceback=redactor.redact_text("".join(traceback.format_exception(exc))),
    )
    atomic_write_json(log_path, event.model_dump(mode="json"), mode=0o600)
    return DiagnosticRecord(event_id=event_id, log_path=log_path, level=level)


def prune_log_days(logs_dir: Path) -> None:
    """Delete YYYY-MM-DD directories under logs_dir beyond the RETAINED_LOG_DAYS newest, skipping any entry that fails."""
    try:
        entries = list(logs_dir.iterdir())
    except OSError:
        return

    day_names = sorted(
        (entry.name for entry in entries if entry.is_dir() and _DAY_DIRECTORY.match(entry.name)), reverse=True
    )
    for name in day_names[RETAINED_LOG_DAYS:]:
        try:
            shutil.rmtree(logs_dir / name)
        except OSError:
            continue


def diagnostic_hint_line(record: DiagnosticRecord | None) -> str | None:
    """Return 'Details logged: <path> (event <id>)' for an error record, else None."""
    if record is None or record.level is not DiagnosticLevel.ERROR:
        return None

    return f"Details logged: {record.log_path} (event {record.event_id})"


def append_diagnostic_hint(message: str, record: DiagnosticRecord | None) -> str:
    """Return message with a 'Details logged: <path> (event <id>)' line for an error record, else message unchanged."""
    line = diagnostic_hint_line(record)
    if line is None:
        return message

    return f"{message}\n{line}"


def error_diagnostics(record: DiagnosticRecord | None) -> list[DiagnosticRecord]:
    """Return [record] for an error-level record, else []."""
    if record is None or record.level is not DiagnosticLevel.ERROR:
        return []

    return [record]
