"""Tier 1 unit tests for BaseResult.error_code field and validator behavior."""

from __future__ import annotations

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult

ERROR_CODE_VALUE_CASES = [
    pytest.param(ErrorCode.GIT_NOT_REPO, "GIT_NOT_REPO", id="enum_member"),
    pytest.param("CUSTOM_CODE", "CUSTOM_CODE", id="plain_string"),
]


class BaseResultTests:
    """Unit tests pinning BaseResult.error_code defaults and current validator behavior."""

    def test_no_errors_or_warnings_defaults_error_code_to_none(self) -> None:
        """[tier-1/unit] BaseResult: no-arg construction equals BaseResult(errors=[], warnings=[], fixes=[], error_code=None)."""
        result = BaseResult()
        assert result == BaseResult(errors=[], warnings=[], fixes=[], error_code=None)

    def test_warnings_only_defaults_error_code_to_none(self) -> None:
        """[tier-1/unit] BaseResult: warnings-only construction equals BaseResult(errors=[], warnings=["careful"], fixes=[], error_code=None)."""
        result = BaseResult(warnings=["careful"])
        assert result == BaseResult(errors=[], warnings=["careful"], fixes=[], error_code=None)

    def test_errors_present_without_error_code_does_not_raise(self) -> None:
        """[tier-1/unit] BaseResult: errors present with no error_code constructs successfully (validator short-circuited); equals BaseResult(errors=["boom"], warnings=[], fixes=[], error_code=None)."""
        result = BaseResult(errors=["boom"])
        assert result == BaseResult(errors=["boom"], warnings=[], fixes=[], error_code=None)

    @pytest.mark.parametrize(("error_code_value", "expected_str"), ERROR_CODE_VALUE_CASES)
    def test_errors_present_with_error_code_value_is_stored(
        self, error_code_value: ErrorCode | str, expected_str: str
    ) -> None:
        """[tier-1/unit] BaseResult: error_code accepts an ErrorCode member or a plain string; equals BaseResult(errors=["boom"], warnings=[], fixes=[], error_code=expected_str)."""
        result = BaseResult(errors=["boom"], error_code=error_code_value)
        assert result == BaseResult(errors=["boom"], warnings=[], fixes=[], error_code=expected_str)
