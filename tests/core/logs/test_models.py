"""Contract tests for session log outcome models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.core.logs import LogsShowResult, LogsShowStatus, RunLogEvent, RunLogEventType


class LogsShowResultOkTests:
    """[tier-1/unit] LogsShowResult.ok: true only for an OK status with no errors."""

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
        """[tier-1/unit] LogsShowResult.ok is True only for status OK with an empty errors list."""
        assert LogsShowResult(status=status, errors=errors).ok is expected_ok


class RunLogEventTests:
    """[tier-1/unit] RunLogEvent: optional field defaults and strict field set."""

    def test_new_fields_default_to_none_and_unknown_field_is_rejected(self) -> None:
        """[tier-1/unit] RunLogEvent: RunLogEvent(event=RunLogEventType.STEP_START) has step_name None and duration_seconds None; an unknown field raises ValidationError."""
        event = RunLogEvent(event=RunLogEventType.STEP_START)

        assert (event.step_name, event.duration_seconds) == (None, None)
        with pytest.raises(ValidationError):
            RunLogEvent.model_validate({"event": RunLogEventType.STEP_START, "unknown_field": 1})
