"""Class-based read service for ``dovo session show``: session details plus step summaries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dovo.common.diagnostic_log import error_diagnostics
from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SessionsRepository
from dovo.core.sessions import (
    HistoryShowResult,
    Session,
    SessionCollection,
    history_redactor,
    reconcile_stale_sessions,
)
from dovo.engine.interrupted_steps import InterruptedStepCloser
from dovo.engine.session_steps import summarize_session_steps


@dataclass
class SessionShowService:
    """Service encapsulating the session show lookup and its redacted step summaries."""

    paths: WorkspacePaths
    sessions_db: SessionsRepository
    sensitive_variables: Sequence[str] = ()
    session_id: str | None = None
    include_logs: bool = False

    def execute(self) -> HistoryShowResult:
        """Reconcile stale sessions, then return the session's details (the latest session when session_id is None) with its redacted step summaries attached.

        The reconciliation warning and diagnostic come before the details' own, on a failure result too.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        reconciliation = reconcile_stale_sessions(
            self.sessions_db, path=self.paths.root_dir, closer=InterruptedStepCloser()
        )
        result = self._details()
        if reconciliation.warning:
            result.warnings.insert(0, reconciliation.warning)
        result.diagnostics[:0] = error_diagnostics(reconciliation.diagnostic)

        if not result.ok or result.session is None:
            return result

        redactor = history_redactor(self.paths, self.sensitive_variables)
        with catch_failure("engine.session_show", session_id=result.session_id) as caught:
            result.steps = summarize_session_steps(result.session, redactor)
        if caught.failure is not None:
            result.warnings.append(caught.failure.message)
            result.diagnostics.extend(error_diagnostics(caught.failure.diagnostic))

        return result

    def _details(self) -> HistoryShowResult:
        """Return the session's details, the latest session's when session_id is None."""
        if self.session_id is None:
            return SessionCollection(
                self.paths, db=self.sessions_db, sensitive_variables=self.sensitive_variables
            ).latest_details(include_logs=self.include_logs)
        return Session(
            self.paths, self.session_id, db=self.sessions_db, sensitive_variables=self.sensitive_variables
        ).details(include_logs=self.include_logs)
