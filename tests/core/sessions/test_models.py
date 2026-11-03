"""Contract tests for session result models."""

from __future__ import annotations

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.sessions import (
    DiffResult,
    HistoryListResult,
    HistoryShowResult,
    LogsShowResult,
    SessionLogEvent,
    SessionLogEventType,
)


class SessionResultOkTests:
    @pytest.mark.parametrize(
        ("error_code", "expected_ok"),
        [
            pytest.param(None, True, id="no_error_code"),
            pytest.param(ErrorCode.SESSION_NOT_FOUND, False, id="session_not_found"),
            pytest.param(ErrorCode.SESSION_LOGS_UNREADABLE, False, id="logs_unreadable"),
            pytest.param(ErrorCode.INTERNAL_ERROR, False, id="internal_error"),
        ],
    )
    def test_ok_is_true_exactly_when_error_code_is_none(self, error_code: ErrorCode | None, expected_ok: bool) -> None:
        errors = [] if error_code is None else ["failed"]
        session_record = SessionRecord(
            project_id="proj-a", session_id="s", blueprint_name="bp", blueprint_key="bp", status=SessionStatus.COMPLETED
        )

        results = (
            DiffResult(error_code=error_code, errors=errors),
            LogsShowResult(error_code=error_code, errors=errors),
            HistoryListResult(error_code=error_code, errors=errors),
            HistoryShowResult(error_code=error_code, errors=errors, session=session_record),
        )

        assert [result.ok for result in results] == [expected_ok] * 4

    def test_result_payloads_carry_no_status_key(self) -> None:
        """Dump each session result and find no 'status' key, so a caller branches on error_code alone."""
        payloads = (
            DiffResult().model_dump(),
            LogsShowResult().model_dump(),
            HistoryListResult().model_dump(),
            HistoryShowResult().model_dump(),
        )

        assert all("status" not in payload for payload in payloads)


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
