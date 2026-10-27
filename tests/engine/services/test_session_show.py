"""Contract tests for engine/services/session_show.py: SessionShowService.execute."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.redact import SecretRedactor
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.sessions import HistoryShowStatus, StepRunSummary
from dovo.engine.services import SessionShowService
from tests.harness.diagnostic_log import diagnostic_record, read_single_event
from tests.harness.sessions import SEEDED_FAILURE, seed_failed_session

_STEPS: list[dict[str, object]] = [{"id": "format", "run": "true"}, {"id": "tests", "run": "false"}]


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

        assert result.status is HistoryShowStatus.OK
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

        assert result.status is HistoryShowStatus.NOT_FOUND
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
        """Return status OK, steps == [] and warnings == [the summarize message], with diagnostics == []."""
        sessions_repo.create("session-x", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED)
        if fault == "corrupt":
            sessions_repo.save_execution_state("session-x", "{not json", expected_revision=0, next_revision=0)

        result = SessionShowService(paths=engine_paths, sessions_db=sessions_repo, session_id="session-x").execute()

        assert result.status is HistoryShowStatus.OK
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
        """With summarize_session_steps raising TypeError("boom"), return the unexpected-error warning and one session.show record."""
        _seed(engine_paths, sessions_repo, "session-failed")

        def _boom(record: SessionRecord, redactor: SecretRedactor) -> list[StepRunSummary]:
            raise TypeError("boom")

        monkeypatch.setattr("dovo.engine.services.session_show.summarize_session_steps", _boom)

        result = SessionShowService(
            paths=engine_paths, sessions_db=sessions_repo, session_id="session-failed"
        ).execute()

        assert result.status is HistoryShowStatus.OK
        assert result.steps == []
        assert result.warnings == ["Unexpected error during session.show: boom"]
        assert result.diagnostics == [diagnostic_record(frozen_diagnostic_clock)]
        event = read_single_event(frozen_diagnostic_clock)
        assert event["operation"] == "session.show"
        assert event["context"] == {"session_id": "session-failed"}
