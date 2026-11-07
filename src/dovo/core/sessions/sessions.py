"""Session and SessionCollection entrypoints."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dovo.common.diagnostic_log import error_diagnostics
from dovo.common.error_codes import ErrorCode
from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import Remediation
from dovo.common.redact import SecretRedactor
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.sessions.exceptions import SessionError, SessionStepLogsAvailable
from dovo.core.sessions.models import (
    DiffResult,
    HistoryListResult,
    HistoryShowResult,
    LogsShowResult,
    LogStreamFilter,
    SessionLogEvent,
    StepStateCloser,
)
from dovo.core.sessions.services.read_diff import read_session_diff
from dovo.core.sessions.services.read_logs import list_session_log_files, read_session_log_events, read_step_logs
from dovo.core.sessions.services.reconcile import reconcile_stale_sessions

_NO_SESSIONS_REMEDIATION = Remediation(
    message="Run 'dovo run <blueprint>' to create a session", command="dovo run <blueprint>"
)
_SESSION_LIST_REMEDIATION = Remediation(
    message="Run 'dovo session list' to view past sessions", command="dovo session list"
)
_SESSION_IDS_REMEDIATION = Remediation(
    message="Run 'dovo session list' to list valid session IDs", command="dovo session list"
)


def _render_run_log_line(event: SessionLogEvent) -> str:
    """Render one session.log event as a short text line of its timestamp, kind, and populated scalar fields."""
    suffix = "".join(f" {name}={value}" for name, value in event.details().items())
    return f"[{event.ts}] {event.event.value}{suffix}"


def history_redactor(paths: WorkspacePaths, sensitive_variables: Sequence[str]) -> SecretRedactor:
    """Build the read-time redactor from the live environment, the listed sensitive variables, and the repository .env."""
    return SecretRedactor.from_environment(credential_envs=sensitive_variables, env_file_dir=paths.root_dir)


def _worktree_outcome(row: SessionRecord, paths: WorkspacePaths) -> tuple[bool | None, Path | None]:
    """Return (worktree_kept, worktree_path) for a finished worktree-backed session, with the path set only when kept; (None, None) for in-place, running, or never-created-worktree sessions."""
    if not row.use_worktree or row.status == SessionStatus.RUNNING:
        return None, None

    if row.worktree_id is None:
        return None, None

    if not row.worktree_kept:
        return False, None

    return True, paths.worktree_dir(row.worktree_id)


def _mask_record(row: SessionRecord, redactor: SecretRedactor) -> SessionRecord:
    """Return ``row`` itself without an error_message, else a detached copy whose error_message is masked."""
    if row.error_message is None:
        return row
    return SessionRecord.model_validate({**row.model_dump(), "error_message": redactor.redact_text(row.error_message)})


def _masked_records(
    db: SessionsRepository, redactor: SecretRedactor, *, limit: int | None, status: str | None
) -> list[SessionRecord]:
    """Return the newest records first, filtered by lifecycle status when given, with error messages masked."""
    status_filter: SessionStatus | str | None = None
    if status is not None:
        try:
            status_filter = SessionStatus(status.lower())
        except ValueError:
            status_filter = status

    return [_mask_record(row, redactor) for row in db.list(limit=limit, status=status_filter)]


def _require_record(db: SessionsRepository, session_id: str, remediation: Remediation) -> SessionRecord:
    """Return the session record for session_id.

    Raises:
        SessionError: SESSION_NOT_FOUND with "Session '<id>' not found." and remediation.
    """
    row = db.get(session_id)
    if row is None:
        raise SessionError(ErrorCode.SESSION_NOT_FOUND, f"Session '{session_id}' not found.", [remediation])
    return row


def _require_latest(db: SessionsRepository) -> SessionRecord:
    """Return the record of the session with the greatest started_at (ties: greatest session id).

    Raises:
        SessionError: SESSION_NOT_FOUND with "No sessions found." when the project has no sessions.
    """
    record = db.get_latest()
    if record is None:
        raise SessionError(ErrorCode.SESSION_NOT_FOUND, "No sessions found.", [_NO_SESSIONS_REMEDIATION])
    return record


def _require_log_dir(paths: WorkspacePaths, session_id: str) -> Path:
    """Return the session's log directory.

    Raises:
        SessionError: SESSION_NOT_FOUND with "No logs found for session '<id>'." when the directory does not exist.
    """
    session_log_dir = paths.logs_dir / session_id
    if not session_log_dir.is_dir():
        raise SessionError(
            ErrorCode.SESSION_NOT_FOUND, f"No logs found for session '{session_id}'.", [_SESSION_LIST_REMEDIATION]
        )
    return session_log_dir


class Session:
    """One session addressed by id: its diff artifact, logs, and run details."""

    def __init__(
        self,
        paths: WorkspacePaths,
        session_id: str,
        db: SessionsRepository | None = None,
        *,
        sensitive_variables: Sequence[str] = (),
    ) -> None:
        self.paths = paths
        self.sensitive_variables = tuple(sensitive_variables)
        self._session_id = session_id
        self.db = db if db is not None else SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)

    @property
    def session_id(self) -> str:
        """Return the session id this handle is bound to."""
        return self._session_id

    def diff(self) -> DiffResult:
        """Return the session's persisted diff, empty when it recorded no changes; report a failure by its error_code."""
        with catch_failure("sessions.diff", session_id=self.session_id) as caught:
            _require_record(self.db, self.session_id, _SESSION_IDS_REMEDIATION)
            artifact_path, diff_text = read_session_diff(self.paths, self.session_id)
        if caught.failure is not None:
            return DiffResult(session_id=self.session_id, **caught.failure.result_fields())
        return DiffResult(session_id=self.session_id, artifact_path=artifact_path, diff_text=diff_text)

    def logs(
        self,
        *,
        step: str | None = None,
        attempt: int | None = None,
        stream: LogStreamFilter = LogStreamFilter.BOTH,
        tail: int | None = None,
    ) -> LogsShowResult:
        """Return the session's parsed session.log events, or one step's capture lines; report a failure by its error_code.

        Without ``step``, ``attempt`` and ``stream`` are ignored because session.log records carry neither concept.
        A step or attempt that was not captured fails with the captured step ids or attempts on the result.
        """
        with catch_failure("sessions.logs", session_id=self.session_id) as caught:
            _require_record(self.db, self.session_id, _SESSION_LIST_REMEDIATION)
            session_log_dir = _require_log_dir(self.paths, self.session_id)
            events, lines = self._read_logs(session_log_dir, step=step, attempt=attempt, stream=stream, tail=tail)
        if caught.failure is not None:
            captured = caught.failure.data
            available = captured if isinstance(captured, SessionStepLogsAvailable) else SessionStepLogsAvailable()
            return LogsShowResult(
                session_id=self.session_id,
                available_steps=list(available.steps),
                available_attempts=list(available.attempts),
                **caught.failure.result_fields(),
            )
        return LogsShowResult(session_id=self.session_id, lines=lines, events=events)

    def details(self, *, include_logs: bool = False) -> HistoryShowResult:
        """Return the session's record, plus log file paths and a session.log tail when requested; report a failure by its error_code."""
        row: SessionRecord | None = None
        with catch_failure("sessions.details", session_id=self.session_id) as caught:
            row = _require_record(self.db, self.session_id, _SESSION_LIST_REMEDIATION)
            row = _mask_record(row, history_redactor(self.paths, self.sensitive_variables))
            worktree_kept, worktree_path = _worktree_outcome(row, self.paths)
            log_files, log_snippet = self._read_log_summary() if include_logs else ([], [])
        if caught.failure is not None:
            return HistoryShowResult(session_id=self.session_id, session=row, **caught.failure.result_fields())
        return HistoryShowResult(
            session_id=self.session_id,
            session=row,
            log_files=log_files,
            log_snippet=log_snippet,
            worktree_kept=worktree_kept,
            worktree_path=worktree_path,
        )

    def _read_logs(
        self,
        session_log_dir: Path,
        *,
        step: str | None,
        attempt: int | None,
        stream: LogStreamFilter,
        tail: int | None,
    ) -> tuple[list[SessionLogEvent], list[str]]:
        """Return (events, []) from session.log when step is None, else ([], masked capture lines) of that step."""
        if step is None:
            return read_session_log_events(session_log_dir, tail=tail), []

        lines = self._read_step_lines(session_log_dir, step=step, attempt=attempt, stream=stream, tail=tail)
        redactor = history_redactor(self.paths, self.sensitive_variables)
        return [], [redactor.redact_text(line) for line in lines]

    def _read_step_lines(
        self,
        session_log_dir: Path,
        *,
        step: str,
        attempt: int | None,
        stream: LogStreamFilter,
        tail: int | None,
    ) -> list[str]:
        """Return the raw capture lines of one step's attempt (the latest when attempt is None) for the selected stream(s).

        Raises:
            SessionError: SESSION_STEP_NOT_FOUND when no capture names step, with the captured step ids as data, or
                SESSION_ATTEMPT_NOT_FOUND when attempt was not captured for step, with the captured attempts as data.
        """
        lines, available_steps, available_attempts = read_step_logs(
            session_log_dir, step=step, attempt=attempt, stream=stream, tail=tail
        )
        if step not in available_steps:
            raise SessionError(
                ErrorCode.SESSION_STEP_NOT_FOUND,
                f"Step '{step}' not found in session '{self.session_id}'. "
                f"Available steps: {', '.join(available_steps) or 'none'}.",
                data=SessionStepLogsAvailable(steps=tuple(available_steps)),
            )
        if attempt is not None and attempt not in available_attempts:
            raise SessionError(
                ErrorCode.SESSION_ATTEMPT_NOT_FOUND,
                f"Attempt {attempt} not found for step '{step}' in session '{self.session_id}'. "
                f"Available attempts: {', '.join(str(a) for a in available_attempts)}.",
                data=SessionStepLogsAvailable(attempts=tuple(available_attempts)),
            )
        return lines

    def _read_log_summary(self) -> tuple[list[str], list[str]]:
        """Return the session's log file paths and its last ten session.log events rendered as text lines."""
        session_log_dir = self.paths.logs_dir / self.session_id
        log_files = [str(p) for p in list_session_log_files(session_log_dir)]
        log_snippet = [_render_run_log_line(e) for e in read_session_log_events(session_log_dir, tail=10)]
        return log_files, log_snippet


class SessionCollection:
    """The project's sessions as a collection."""

    def __init__(
        self, paths: WorkspacePaths, db: SessionsRepository | None = None, *, sensitive_variables: Sequence[str] = ()
    ) -> None:
        self.paths = paths
        self.sensitive_variables = tuple(sensitive_variables)
        self.db = db if db is not None else SessionsRepository(db_path=paths.database_file, project_id=paths.project_id)

    def get(self, session_id: str) -> Session:
        """Return a Session handle sharing this collection's paths and run repository."""
        return Session(self.paths, session_id, db=self.db, sensitive_variables=self.sensitive_variables)

    def list(self, *, closer: StepStateCloser, limit: int | None = 20, status: str | None = None) -> HistoryListResult:
        """Reconcile stale sessions, closing the interrupted steps of each one marked failed with closer, then list this project's session records, newest first; report a failure by its error_code.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        reconciliation = reconcile_stale_sessions(self.db, path=self.paths.root_dir, closer=closer)
        warnings = [reconciliation.warning] if reconciliation.warning else []
        diagnostics = error_diagnostics(reconciliation.diagnostic)
        redactor = history_redactor(self.paths, self.sensitive_variables)
        with catch_failure("sessions.list", path=str(self.paths.root_dir)) as caught:
            sessions = _masked_records(self.db, redactor, limit=limit, status=status)
        if caught.failure is not None:
            return HistoryListResult(warnings=warnings, **caught.failure.result_fields(prior_diagnostics=diagnostics))
        return HistoryListResult(sessions=sessions, warnings=warnings, diagnostics=diagnostics)

    def latest_details(self, *, include_logs: bool = False) -> HistoryShowResult:
        """Return the latest session's details; report a failure by its error_code."""
        with catch_failure("sessions.latest_details", path=str(self.paths.root_dir)) as caught:
            latest = self._latest()
        if caught.failure is not None:
            return HistoryShowResult(**caught.failure.result_fields())
        return latest.details(include_logs=include_logs)

    def latest_logs(
        self,
        *,
        step: str | None = None,
        attempt: int | None = None,
        stream: LogStreamFilter = LogStreamFilter.BOTH,
        tail: int | None = None,
    ) -> LogsShowResult:
        """Return the latest session's logs; report a failure by its error_code."""
        with catch_failure("sessions.latest_logs", path=str(self.paths.root_dir)) as caught:
            latest = self._latest()
        if caught.failure is not None:
            return LogsShowResult(**caught.failure.result_fields())
        return latest.logs(step=step, attempt=attempt, stream=stream, tail=tail)

    def latest_diff(self) -> DiffResult:
        """Return the latest session's diff; report a failure by its error_code."""
        with catch_failure("sessions.latest_diff", path=str(self.paths.root_dir)) as caught:
            latest = self._latest()
        if caught.failure is not None:
            return DiffResult(**caught.failure.result_fields())
        return latest.diff()

    def _latest(self) -> Session:
        """Return the handle of the session with the greatest started_at (ties: greatest session id).

        Raises:
            SessionError: SESSION_NOT_FOUND when the project has no sessions.
        """
        return self.get(_require_latest(self.db).session_id)
