"""Contract tests for engine/services/session_show.py: SessionShowService.execute."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.redact import SecretRedactor
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.sessions import STALE_RUN_ERROR_MESSAGE, StepRunSummary, current_process_started_at
from dovo.engine.services import SessionShowService
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.sessions import (
    INTERRUPTED_ATTEMPT_MESSAGE,
    SEEDED_FAILURE,
    fail_reconciliation_list,
    seed_dead_session,
    seed_failed_session,
)

_STEPS: list[dict[str, object]] = [{"id": "format", "run": "true"}, {"id": "tests", "run": "false"}]


_DEAD_WARNING = "Marked 1 interrupted session failed (session_id: session-dead)."


def _seed(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
    seed_failed_session(paths, sessions, session_id=session_id, steps=_STEPS, failed_step_id="tests")


class SessionShowServiceTests:
    def test_execute_with_session_id_returns_details_with_redacted_steps(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo, "session-failed")

        result = SessionShowService(
            paths=engine_paths, sessions_db=sessions_repo, session_id="session-failed"
        ).execute()

        assert result.error_code is None
        assert result.session is not None
        assert result.session.session_id == "session-failed"
        assert [(step.step_id, step.state, step.error_message) for step in result.steps] == [
            ("format", "completed", None),
            ("tests", "failed", SEEDED_FAILURE),
        ]
        assert result.warnings == []

    def test_execute_without_session_id_returns_the_latest_session(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo, "session-older")
        _seed(engine_paths, sessions_repo, "session-newer")

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo).execute()

        assert result.session_id == "session-newer"
        assert [step.step_id for step in result.steps] == ["format", "tests"]

    def test_execute_unknown_session_returns_not_found_without_steps(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id="ghost").execute()

        assert result.error_code == ErrorCode.SESSION_NOT_FOUND
        assert result.session is None
        assert result.steps == []

    @pytest.mark.parametrize(
        ("fault", "warning"),
        [
            pytest.param("no-state", "No step data recorded for this session.", id="no-state"),
            pytest.param(
                "corrupt",
                "Step data could not be read: Invalid JSON: key must be a string at line 1 column 2",
                id="corrupt",
            ),
        ],
    )
    def test_execute_step_failure_adds_message_to_warnings(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, fault: str, warning: str
    ) -> None:
        """Return error_code None, steps == [] and warnings == [the summarize message], with diagnostics == []."""
        sessions_repo.create("session-x", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
        if fault == "corrupt":
            sessions_repo.save_execution_state("session-x", "{not json", expected_revision=0, next_revision=0)

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id="session-x").execute()

        assert result.error_code is None
        assert result.steps == []
        assert result.warnings == [warning]
        assert result.diagnostics == []

    def test_execute_unexpected_step_error_is_recorded_as_warning(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        """With summarize_session_steps raising TypeError("boom"), return the unexpected-error warning and one engine.session_show record."""
        _seed(engine_paths, sessions_repo, "session-failed")

        def _boom(record: SessionRecord, redactor: SecretRedactor) -> list[StepRunSummary]:
            raise TypeError("boom")

        monkeypatch.setattr("dovo.engine.services.session_show.summarize_session_steps", _boom)

        result = SessionShowService(
            paths=engine_paths, sessions_db=sessions_repo, session_id="session-failed"
        ).execute()

        assert result.error_code is None
        assert result.steps == []
        assert result.warnings == ["Unexpected error during engine.session_show: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "engine.session_show"
        assert event["context"] == {"session_id": "session-failed"}


class SessionShowReconcileTests:
    @pytest.mark.parametrize("session_id", ["session-dead", None], ids=["explicit-id", "latest"])
    def test_execute_reconciles_dead_session_and_closes_its_step(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, session_id: str | None
    ) -> None:
        """A running row whose process is gone and that cannot pause, with a running tests step, is shown failed with the step failed."""
        seed_dead_session(engine_paths, sessions_repo, session_id="session-dead")

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id=session_id).execute()

        assert result.error_code is None
        assert result.session is not None
        assert result.session.status is SessionStatus.FAILED
        assert result.session.error_message == STALE_RUN_ERROR_MESSAGE
        assert result.warnings == [_DEAD_WARNING]
        assert [(step.step_id, step.state, step.exit_code, step.error_message) for step in result.steps] == [
            ("format", "pending", None, None),
            ("tests", "failed", 1, INTERRUPTED_ATTEMPT_MESSAGE),
        ]

    def test_execute_live_session_is_running_without_warning(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        sessions_repo.create("session-live", "bp", "bp", pid=os.getpid(), pid_started_at=current_process_started_at())

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id="session-live").execute()

        assert result.session is not None
        assert result.session.status is SessionStatus.RUNNING
        assert result.warnings == ["No step data recorded for this session."]

    def test_execute_failed_session_without_state_keeps_missing_state_warning(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        sessions_repo.create("session-dead", "bp", "bp")

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id="session-dead").execute()

        assert result.session is not None
        assert result.session.status is SessionStatus.FAILED
        assert result.warnings == [_DEAD_WARNING, "No step data recorded for this session."]

    def test_execute_unknown_session_keeps_reconciliation_warning(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        sessions_repo.create("session-dead", "bp", "bp")

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id="ghost").execute()

        assert result.error_code == ErrorCode.SESSION_NOT_FOUND
        assert result.warnings == [_DEAD_WARNING]

    def test_execute_reconciliation_failure_is_reported_as_diagnostic(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
    ) -> None:
        _seed(engine_paths, sessions_repo, "session-failed")
        fail_reconciliation_list(monkeypatch)

        result = SessionShowService(
            paths=engine_paths, sessions_db=sessions_repo, session_id="session-failed"
        ).execute()

        assert result.error_code is None
        assert result.session_id == "session-failed"
        assert result.warnings == []
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
