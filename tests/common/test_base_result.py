"""Tier 1 unit tests for BaseResult.error_code field and validator behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult, Remediation

ERROR_CODE_VALUE_CASES = [
    pytest.param(ErrorCode.GIT_NOT_REPO, "GIT_NOT_REPO", id="enum_member"),
    pytest.param("CUSTOM_CODE", "CUSTOM_CODE", id="plain_string"),
]


class BaseResultTests:
    """Unit tests pinning BaseResult.error_code defaults and current validator behavior."""

    def test_no_errors_or_warnings_defaults_error_code_to_none(self) -> None:
        result = BaseResult()
        assert result == BaseResult(errors=[], warnings=[], error_code=None)

    def test_warnings_only_defaults_error_code_to_none(self) -> None:
        result = BaseResult(warnings=["careful"])
        assert result == BaseResult(errors=[], warnings=["careful"], error_code=None)

    def test_errors_present_without_error_code_does_not_raise(self) -> None:
        result = BaseResult(errors=["boom"])
        assert result == BaseResult(errors=["boom"], warnings=[], error_code=None)

    @pytest.mark.parametrize(("error_code_value", "expected_str"), ERROR_CODE_VALUE_CASES)
    def test_errors_present_with_error_code_value_is_stored(
        self, error_code_value: ErrorCode | str, expected_str: str
    ) -> None:
        """Accept an ErrorCode member or a plain string as error_code."""
        result = BaseResult(errors=["boom"], error_code=error_code_value)
        assert result == BaseResult(errors=["boom"], warnings=[], error_code=expected_str)

    def test_defaults_remediations_to_empty(self) -> None:
        """Default BaseResult().remediations to [] and dump it as "remediations": []."""
        result = BaseResult()

        assert result.remediations == []
        assert result.model_dump(mode="json")["remediations"] == []

    def test_rejects_fixes_keyword(self) -> None:
        """Raise ValidationError when any BaseResult is built with fixes=[...]."""
        with pytest.raises(ValidationError):
            BaseResult.model_validate({"fixes": ["Run it"]})


class RemediationModelTests:
    """Unit tests pinning the Remediation field constraints and its JSON shape."""

    @pytest.mark.parametrize(
        "kwargs", [{"message": ""}, {"message": "Run it", "command": ""}], ids=["empty-message", "empty-command"]
    )
    def test_rejects_empty_message_or_command(self, kwargs: dict[str, str]) -> None:
        """Raise ValidationError for an empty message or an empty command."""
        with pytest.raises(ValidationError):
            Remediation(**kwargs)

    def test_dumps_both_keys_with_null_command(self) -> None:
        """Dump Remediation(message="Check it") to {"message": "Check it", "command": None}."""
        assert Remediation(message="Check it").model_dump(mode="json") == {"message": "Check it", "command": None}
