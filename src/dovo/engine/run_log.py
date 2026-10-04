"""Best-effort JSON Lines writer for the session run.log timeline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dovo.common.constants import RUN_LOG_FILENAME
from dovo.core.sessions.logs import RunLogEvent


def append_run_log_event(session_log_dir: Path | None, event: RunLogEvent) -> None:
    """Stamp event.ts and append it as one JSON line to session_log_dir/run.log, best-effort."""
    if session_log_dir is None:
        return

    stamped = event.model_copy(update={"ts": datetime.now(UTC).isoformat()})
    try:
        with (session_log_dir / RUN_LOG_FILENAME).open("a", encoding="utf-8") as run_log:
            run_log.write(stamped.model_dump_json() + "\n")
    except OSError:
        # Best-effort timeline: a run.log write failure never affects the run.
        pass
