"""Tier 1 unit tests for the exit each ErrorCode declares."""

from __future__ import annotations

import pytest

from dovo.common.error_codes import ErrorCode

_ARGUMENT_CODES = {
    ErrorCode.PROJECT_ID_INVALID,
    ErrorCode.CONFIG_KEY_PATH_INVALID,
    ErrorCode.AGENT_ENV_OVERRIDE_INVALID,
    ErrorCode.INTERACTION_ARGUMENTS_INVALID,
    ErrorCode.CATALOG_TIER_CONFLICT,
}


def _expected_exit(code: ErrorCode) -> int:
    """Return 2 for the argument-decided codes, 130 for RUN_CANCELLED and SESSION_PROCESS_STOP_FAILED, and 1 for every other code."""
    if code in _ARGUMENT_CODES:
        return 2
    if code in (ErrorCode.RUN_CANCELLED, ErrorCode.SESSION_PROCESS_STOP_FAILED):
        return 130
    return 1


class ErrorCodeExitTests:
    @pytest.mark.parametrize("code", list(ErrorCode), ids=lambda code: code.name)
    def test_every_code_declares_its_exit(self, code: ErrorCode) -> None:
        assert code.exit_code == _expected_exit(code)

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            pytest.param(ErrorCode.PROJECT_ID_INVALID, 2, id="argument"),
            pytest.param(ErrorCode.CATALOG_TIER_CONFLICT, 2, id="tier_conflict"),
            pytest.param(ErrorCode.INTERACTION_ARGUMENT_FILE_UNREADABLE, 1, id="unreadable_file"),
            pytest.param(ErrorCode.INTERACTION_ANSWER_INVALID, 1, id="answer_rejected_by_prompt"),
            pytest.param(ErrorCode.SESSION_NOT_FOUND, 1, id="not_found"),
            pytest.param(ErrorCode.RUN_CANCELLED, 130, id="cancelled"),
            pytest.param(ErrorCode.SESSION_PROCESS_STOP_FAILED, 130, id="process_stop_failed"),
        ],
    )
    def test_exit_of_representative_codes(self, code: ErrorCode, expected: int) -> None:
        assert code.exit_code == expected
