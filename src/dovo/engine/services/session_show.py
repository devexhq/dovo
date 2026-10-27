"""Class-based read service for ``dovo session show``: session details plus step summaries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dovo.common.diagnostic_log import error_diagnostics
from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.core.db import SessionsRepository
from dovo.core.sessions import HistoryShowResult, Session, SessionCollection, history_redactor
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
        """Return the session's details (the latest session when session_id is None) with its redacted step summaries attached."""
        if self.session_id is None:
            result = SessionCollection(
                self.paths, db=self.sessions_db, sensitive_variables=self.sensitive_variables
            ).latest_details(include_logs=self.include_logs)
        else:
            result = Session(
                self.paths, self.session_id, db=self.sessions_db, sensitive_variables=self.sensitive_variables
            ).details(include_logs=self.include_logs)

        if result.session is None:
            return result

        redactor = history_redactor(self.paths, self.sensitive_variables)
        with catch_failure("engine.session_show", session_id=result.session_id) as caught:
            result.steps = summarize_session_steps(result.session, redactor)
        if caught.failure is not None:
            result.warnings.append(caught.failure.message)
            result.diagnostics.extend(error_diagnostics(caught.failure.diagnostic))

        return result
