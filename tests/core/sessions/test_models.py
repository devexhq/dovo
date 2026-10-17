"""Contract tests for session result models."""

from __future__ import annotations

import pytest

from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.sessions import (
    HistoryListResult,
    HistoryShowResult,
    HistoryShowStatus,
    LogsShowResult,
    LogsShowStatus,
    SessionLogEvent,
    SessionLogEventType,
)


class LogsShowResultOkTests:
    @pytest.mark.parametrize(
        ("status", "errors", "expected_ok"),
        [
            pytest.param(LogsShowStatus.OK, [], True, id="ok"),
            pytest.param(LogsShowStatus.OK, ["Failed reading session logs"], False, id="ok_with_errors"),
            pytest.param(LogsShowStatus.SESSION_NOT_FOUND, [], False, id="session_not_found"),
            pytest.param(LogsShowStatus.STEP_NOT_FOUND, [], False, id="step_not_found"),
            pytest.param(LogsShowStatus.ATTEMPT_NOT_FOUND, [], False, id="attempt_not_found"),
        ],
    )
    def test_ok_reflects_status_and_errors(self, status: LogsShowStatus, errors: list[str], expected_ok: bool) -> None:
        assert LogsShowResult(status=status, errors=errors).ok is expected_ok


class SessionLogEventContractTests:
    @pytest.mark.parametrize(
        ("event", "details"),
        [
            pytest.param(
                SessionLogEvent(event=SessionLogEventType.AGENT_ENV_FILTERED, step_id="s", env_withheld="A,B"),
                {"step_id": "s", "env_withheld": "A,B"},
                id="agent_env_filtered",
            ),
            pytest.param(
                SessionLogEvent(event=SessionLogEventType.AGENT_PROGRESS, step_id="s", kind="edit", target="a.txt"),
                {"step_id": "s", "kind": "edit", "target": "a.txt"},
                id="agent_progress",
            ),
        ],
    )
    def test_event_round_trips_and_details_lists_its_fields(
        self, event: SessionLogEvent, details: dict[str, str]
    ) -> None:
        restored = SessionLogEvent.model_validate_json(event.model_dump_json())

        assert restored == event
        assert restored.details() == details


class HistoryListResultTests:
    @pytest.mark.parametrize(
        ("errors", "expected_ok"),
        [pytest.param([], True, id="no_errors"), pytest.param(["boom"], False, id="errors")],
    )
    def test_ok_is_false_only_when_errors_are_present(self, errors: list[str], expected_ok: bool) -> None:
        assert HistoryListResult(errors=errors).ok is expected_ok


class HistoryShowResultTests:
    @pytest.mark.parametrize(
        ("has_run", "errors", "expected_ok"),
        [
            pytest.param(True, [], True, id="run_without_errors"),
            pytest.param(True, ["Failed reading session logs"], False, id="run_with_errors"),
            pytest.param(False, [], False, id="ok_status_without_run"),
        ],
    )
    def test_ok_requires_run_record_and_no_errors(self, has_run: bool, errors: list[str], expected_ok: bool) -> None:
        session_record = SessionRecord(
            session_id="s", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )

        result = HistoryShowResult(
            status=HistoryShowStatus.OK, session=session_record if has_run else None, errors=errors
        )

        assert result.ok is expected_ok
