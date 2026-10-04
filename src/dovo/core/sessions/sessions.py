"""Session and SessionCollection entrypoints."""

from __future__ import annotations

from pathlib import Path

from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import RunsRepository, RunStatus
from dovo.core.sessions.models import (
    DiffResult,
    DiffStatus,
    HistoryListResult,
    HistoryListStatus,
    HistoryShowResult,
    HistoryShowStatus,
    LogsShowResult,
    LogsShowStatus,
    LogStreamFilter,
    RunLogEvent,
)
from dovo.core.sessions.services.read_diff import read_session_diff
from dovo.core.sessions.services.read_logs import list_session_log_files, read_run_log_events, read_step_logs
from dovo.core.sessions.services.reconcile import reconcile_stale_runs


def _render_run_log_line(event: RunLogEvent) -> str:
    """Render one run.log event as a short text line of its timestamp, kind, and populated scalar fields."""
    suffix = "".join(f" {name}={value}" for name, value in event.details().items())
    return f"[{event.ts}] {event.event.value}{suffix}"


class Session:
    """One session addressed by id: its diff artifact, logs, and run details."""

    def __init__(self, paths: WorkspacePaths, session_id: str, db: RunsRepository | None = None) -> None:
        self.paths = paths
        self._session_id = session_id
        self.db = db if db is not None else RunsRepository(db_path=paths.database_file, project_id=paths.project_id)

    @property
    def session_id(self) -> str:
        """Return the session id this handle is bound to."""
        return self._session_id

    def diff(self) -> DiffResult:
        """Return the session's persisted diff, or SESSION_NOT_FOUND when it has no run record."""
        if self.db.get(self.session_id) is None:
            return DiffResult(
                status=DiffStatus.SESSION_NOT_FOUND,
                session_id=self.session_id,
                errors=[f"Session '{self.session_id}' not found under .dovo/sessions/."],
                fixes=["Run `dovo worktree list` or check .dovo/sessions/ for valid session IDs"],
            )

        return read_session_diff(self.paths, self.session_id)

    def logs(
        self,
        *,
        step: str | None = None,
        attempt: int | None = None,
        stream: LogStreamFilter = LogStreamFilter.BOTH,
        tail: int | None = None,
    ) -> LogsShowResult:
        """Look up the session's persisted logs and return the filtered events/lines or a not-found status.

        Without ``step``, returns parsed run.log events; ``attempt`` and ``stream`` are ignored
        because run.log records carry neither concept. An unreadable log file is reported in ``errors``.
        """
        if self.db.get(self.session_id) is None:
            return LogsShowResult(status=LogsShowStatus.SESSION_NOT_FOUND, session_id=self.session_id)

        session_log_dir = self.paths.logs_dir / self.session_id
        if not session_log_dir.is_dir():
            return LogsShowResult(status=LogsShowStatus.SESSION_NOT_FOUND, session_id=self.session_id)

        if step is None:
            try:
                events = read_run_log_events(session_log_dir, tail=tail)
            except OSError as exc:
                return LogsShowResult(
                    status=LogsShowStatus.OK,
                    session_id=self.session_id,
                    errors=[f"Failed reading session logs in '{session_log_dir}': {exc}"],
                )
            return LogsShowResult(status=LogsShowStatus.OK, session_id=self.session_id, events=events)

        return self._read_step_logs(session_log_dir, step=step, attempt=attempt, stream=stream, tail=tail)

    def details(self, *, include_logs: bool = False) -> HistoryShowResult:
        """Look up the session's run record, plus log file paths and a run.log tail when requested."""
        row = self.db.get(self.session_id)
        if row is None:
            return HistoryShowResult(status=HistoryShowStatus.NOT_FOUND, session_id=self.session_id)

        if not include_logs:
            return HistoryShowResult(status=HistoryShowStatus.OK, session_id=self.session_id, run=row)

        session_log_dir = self.paths.logs_dir / self.session_id
        try:
            log_files = [str(p) for p in list_session_log_files(session_log_dir)]
            log_snippet = [_render_run_log_line(e) for e in read_run_log_events(session_log_dir, tail=10)]
        except OSError as exc:
            return HistoryShowResult(
                status=HistoryShowStatus.OK,
                session_id=self.session_id,
                run=row,
                errors=[f"Failed reading session logs in '{session_log_dir}': {exc}"],
            )

        return HistoryShowResult(
            status=HistoryShowStatus.OK,
            session_id=self.session_id,
            run=row,
            log_files=log_files,
            log_snippet=log_snippet,
        )

    def _read_step_logs(
        self,
        session_log_dir: Path,
        *,
        step: str,
        attempt: int | None,
        stream: LogStreamFilter,
        tail: int | None,
    ) -> LogsShowResult:
        """Read one step's capture lines, or classify an unreadable file, unknown step, or unknown attempt."""
        try:
            lines, available_steps, available_attempts = read_step_logs(
                session_log_dir, step=step, attempt=attempt, stream=stream, tail=tail
            )
        except OSError as exc:
            return LogsShowResult(
                status=LogsShowStatus.OK,
                session_id=self.session_id,
                errors=[f"Failed reading session logs in '{session_log_dir}': {exc}"],
            )

        if step not in available_steps:
            return LogsShowResult(
                status=LogsShowStatus.STEP_NOT_FOUND, session_id=self.session_id, available_steps=available_steps
            )
        if attempt is not None and attempt not in available_attempts:
            return LogsShowResult(
                status=LogsShowStatus.ATTEMPT_NOT_FOUND,
                session_id=self.session_id,
                available_attempts=available_attempts,
            )

        return LogsShowResult(status=LogsShowStatus.OK, session_id=self.session_id, lines=lines)


class SessionCollection:
    """The project's sessions as a collection."""

    def __init__(self, paths: WorkspacePaths, db: RunsRepository | None = None) -> None:
        self.paths = paths
        self.db = db if db is not None else RunsRepository(db_path=paths.database_file, project_id=paths.project_id)

    def get(self, session_id: str) -> Session:
        """Return a Session handle sharing this collection's paths and run repository."""
        return Session(self.paths, session_id, db=self.db)

    def list(self, *, limit: int | None = 20, status: str | None = None) -> HistoryListResult:
        """Reconcile stale runs, then list this project's run records, newest first."""
        warnings: list[str] = []
        reconciliation_result = reconcile_stale_runs(self.db, path=self.paths.root_dir)
        if reconciliation_result.warning:
            warnings.append(reconciliation_result.warning)

        status_filter: RunStatus | str | None = None
        if status is not None:
            try:
                status_filter = RunStatus(status.lower())
            except ValueError:
                status_filter = status

        runs = self.db.list(limit=limit, status=status_filter)
        return HistoryListResult(status=HistoryListStatus.OK, runs=runs, warnings=warnings)

    def latest_diff(self) -> DiffResult:
        """Return the diff of the run with the greatest started_at (ties: greatest id), or SESSION_NOT_FOUND."""
        runs = self.db.list(limit=1)
        if not runs:
            return DiffResult(
                status=DiffStatus.SESSION_NOT_FOUND,
                errors=["No sessions found under .dovo/sessions/."],
                fixes=["Run `dovo worktree list` or check .dovo/sessions/ for valid session IDs"],
            )

        return read_session_diff(self.paths, runs[0].session_id)
