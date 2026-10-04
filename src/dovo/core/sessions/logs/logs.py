"""Logs domain entrypoint coordinator."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import RunsRepository
from dovo.core.sessions.logs.models import LogsShowResult, LogsShowStatus, LogStreamFilter
from dovo.core.sessions.logs.services.read import read_run_log_events, read_step_logs


class Logs:
    """Unified entrypoint for persisted session log inspection."""

    def __init__(self, paths: WorkspacePaths, db: RunsRepository | None = None) -> None:
        self.paths = paths
        self.db = db if db is not None else RunsRepository(db_path=paths.database_file, project_id=paths.project_id)

    def show(
        self,
        session_id: str,
        *,
        step: str | None = None,
        attempt: int | None = None,
        stream: LogStreamFilter = LogStreamFilter.BOTH,
        tail: int | None = None,
    ) -> LogsShowResult:
        """Look up a session's persisted logs and return the filtered events/lines or a not-found status.

        Without ``step``, returns parsed run.log events; ``attempt`` and ``stream`` are ignored
        because run.log records carry neither concept. An unreadable log file is reported in ``errors``.
        """
        if self.db.get(session_id) is None:
            return LogsShowResult(status=LogsShowStatus.SESSION_NOT_FOUND, session_id=session_id)

        session_log_dir = self.paths.logs_dir / session_id
        if not session_log_dir.is_dir():
            return LogsShowResult(status=LogsShowStatus.SESSION_NOT_FOUND, session_id=session_id)

        try:
            if step is None:
                events = read_run_log_events(session_log_dir, tail=tail)
                return LogsShowResult(status=LogsShowStatus.OK, session_id=session_id, events=events)

            lines, available_steps, available_attempts = read_step_logs(
                session_log_dir, step=step, attempt=attempt, stream=stream, tail=tail
            )
        except OSError as exc:
            return LogsShowResult(
                status=LogsShowStatus.OK,
                session_id=session_id,
                errors=[f"Failed reading session logs in '{session_log_dir}': {exc}"],
            )

        if step not in available_steps:
            return LogsShowResult(
                status=LogsShowStatus.STEP_NOT_FOUND, session_id=session_id, available_steps=available_steps
            )
        if attempt is not None and attempt not in available_attempts:
            return LogsShowResult(
                status=LogsShowStatus.ATTEMPT_NOT_FOUND, session_id=session_id, available_attempts=available_attempts
            )

        return LogsShowResult(status=LogsShowStatus.OK, session_id=session_id, lines=lines)
