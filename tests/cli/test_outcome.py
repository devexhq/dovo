"""Tier 1 unit tests for exit_for: a command's exit follows its result's error_code."""

from __future__ import annotations

import pytest
import typer

from dovo.cli.outcome import exit_for
from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult


class ExitForTests:
    def test_success_returns_without_exiting(self) -> None:
        assert exit_for(BaseResult()) is None

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            pytest.param(ErrorCode.SESSION_NOT_FOUND, 1, id="workspace_failure"),
            pytest.param(ErrorCode.PROJECT_ID_INVALID, 2, id="argument_failure"),
            pytest.param(ErrorCode.RUN_CANCELLED, 130, id="cancelled_run"),
        ],
    )
    def test_failure_exits_by_its_error_code(self, code: ErrorCode, expected: int) -> None:
        with pytest.raises(typer.Exit) as raised:
            exit_for(BaseResult(error_code=code, errors=["boom"]))

        assert raised.value.exit_code == expected
