"""Contract tests for session log outcome models."""

from __future__ import annotations

import pytest

from worktree.core.logs import LogsShowResult, LogsShowStatus


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
