"""Best-effort JSON Lines writer for the session session.log timeline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dovo.common.constants import SESSION_LOG_FILENAME
from dovo.core.sessions import SessionLogEvent


def append_session_log_event(session_log_dir: Path | None, event: SessionLogEvent) -> None:
    """Stamp event.ts and append it as one JSON line to session_log_dir/session.log, best-effort."""
    if session_log_dir is None:
        return

    stamped = event.model_copy(update={"ts": datetime.now(UTC).isoformat()})
    try:
        with (session_log_dir / SESSION_LOG_FILENAME).open("a", encoding="utf-8") as session_log:
            session_log.write(stamped.model_dump_json() + "\n")
    except OSError:
        # Best-effort timeline: a session.log write failure never affects the run.
        pass
