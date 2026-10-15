"""Contract tests for session result models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.sessions import (
    HistoryListResult,
    HistoryListStatus,
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


class RunLogEventTests:
    def test_new_fields_default_to_none_and_unknown_field_is_rejected(self) -> None:
        event = SessionLogEvent(event=SessionLogEventType.STEP_START)

        assert (event.step_name, event.duration_seconds) == (None, None)
        with pytest.raises(ValidationError):
            SessionLogEvent.model_validate({"event": SessionLogEventType.STEP_START, "unknown_field": 1})


class SessionLogEventContractTests:
    def test_agent_env_filtered_event_round_trips_and_details_lists_env_withheld(self) -> None:
        event = SessionLogEvent(event=SessionLogEventType.AGENT_ENV_FILTERED, step_id="s", env_withheld="A,B")

        restored = SessionLogEvent.model_validate_json(event.model_dump_json())

        assert restored == event
        assert restored.details() == {"step_id": "s", "env_withheld": "A,B"}


class SessionLogEventProgressTests:
    def test_agent_progress_event_round_trips_and_details_lists_kind_and_target(self) -> None:
        event = SessionLogEvent(event=SessionLogEventType.AGENT_PROGRESS, step_id="s", kind="edit", target="a.txt")

        restored = SessionLogEvent.model_validate_json(event.model_dump_json())

        assert restored == event
        assert restored.details() == {"step_id": "s", "kind": "edit", "target": "a.txt"}


class HistoryListResultTests:
    def test_defaults_describe_an_empty_ok_listing(self) -> None:
        result = HistoryListResult()

        assert (result.status, result.sessions, result.errors, result.warnings, result.fixes, result.error_code) == (
            HistoryListStatus.OK,
            [],
            [],
            [],
            [],
            None,
        )
        assert result.ok is True

    def test_ok_is_false_when_errors_are_present(self) -> None:
        assert HistoryListResult(errors=["boom"]).ok is False


class HistoryShowResultTests:
    def test_defaults_leave_run_and_log_fields_empty(self) -> None:
        result = HistoryShowResult(status=HistoryShowStatus.NOT_FOUND)

        assert (
            result.session_id,
            result.session,
            result.log_files,
            result.log_snippet,
            result.errors,
            result.warnings,
            result.fixes,
            result.error_code,
        ) == (None, None, [], [], [], [], [], None)
        assert result.ok is False

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
