"""Tier 1 unit tests for BaseResult.error_code and the rule that it is set exactly when errors is non-empty."""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from pydantic import ValidationError

import dovo
from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult, Remediation

_MIGRATION_PACKAGE = "dovo.core.db.alembic"
MISMATCH_MESSAGE = "error_code must be set exactly when errors is non-empty"

MISMATCH_CASES = [
    pytest.param({"errors": ["boom"]}, id="errors_without_code"),
    pytest.param({"error_code": ErrorCode.GIT_FAILED}, id="code_without_errors"),
    pytest.param({"error_code": ErrorCode.GIT_FAILED, "warnings": ["careful"]}, id="code_with_only_warnings"),
    pytest.param(
        {"error_code": ErrorCode.GIT_FAILED, "remediations": [Remediation(message="Retry")]},
        id="code_with_only_remediations",
    ),
]

UNPUBLISHED_CODE_CASES = [
    pytest.param("CUSTOM_CODE", id="unknown_name"),
    pytest.param("GIT_FAILED", id="member_value_as_string"),
]

NO_ERROR_CASES = [
    pytest.param({}, id="nothing"),
    pytest.param({"warnings": ["careful"]}, id="only_warnings"),
    pytest.param({"remediations": [Remediation(message="Retry")]}, id="only_remediations"),
]


def _command_result_types() -> list[type[BaseResult]]:
    """Import every dovo module outside the migration scripts and return each BaseResult subclass that builds with no arguments."""
    for module in pkgutil.walk_packages(dovo.__path__, "dovo."):
        if not module.name.startswith(_MIGRATION_PACKAGE):
            importlib.import_module(module.name)
    found: list[type[BaseResult]] = []
    pending = list(BaseResult.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if all(not field.is_required() for field in cls.model_fields.values()):
            found.append(cls)
    return sorted(set(found), key=lambda cls: cls.__qualname__)


class BaseResultTests:
    """Unit tests pinning BaseResult.error_code defaults and the refusal of a code that disagrees with errors."""

    @pytest.mark.parametrize("fields", MISMATCH_CASES)
    def test_mismatched_errors_and_code_are_refused(self, fields: dict[str, object]) -> None:
        """Refuse errors with no code, and a code with no errors, with the published message."""
        with pytest.raises(ValidationError, match=MISMATCH_MESSAGE):
            BaseResult.model_validate(fields)

    @pytest.mark.parametrize("code", UNPUBLISHED_CODE_CASES)
    def test_code_outside_the_published_set_is_refused(self, code: str) -> None:
        """Refuse a plain string error_code, even one spelling a member's value."""
        with pytest.raises(ValidationError) as caught:
            BaseResult.model_validate({"errors": ["boom"], "error_code": code})

        assert [error["loc"] for error in caught.value.errors()] == [("error_code",)]

    @pytest.mark.parametrize("fields", NO_ERROR_CASES)
    def test_result_without_errors_carries_no_code(self, fields: dict[str, object]) -> None:
        """Build a result with only warnings, only remediations or nothing with error_code None."""
        assert BaseResult.model_validate(fields).error_code is None

    def test_errors_with_a_published_code_are_stored(self) -> None:
        """Store an ErrorCode member beside errors and dump its value in JSON mode."""
        result = BaseResult(errors=["boom"], error_code=ErrorCode.GIT_FAILED)

        assert result.error_code is ErrorCode.GIT_FAILED
        assert result.model_dump(mode="json")["error_code"] == "GIT_FAILED"

    @pytest.mark.parametrize("result_type", _command_result_types(), ids=lambda cls: cls.__qualname__)
    def test_refusal_applies_to_every_command_result(self, result_type: type[BaseResult]) -> None:
        """Refuse errors with no code on every BaseResult subclass in dovo."""
        with pytest.raises(ValidationError, match=MISMATCH_MESSAGE):
            result_type(errors=["boom"])

    def test_defaults_remediations_to_empty(self) -> None:
        """Default BaseResult().remediations to [] and dump it as "remediations": []."""
        result = BaseResult()

        assert result.remediations == []
        assert result.model_dump(mode="json")["remediations"] == []

    def test_defaults_diagnostics_to_empty(self) -> None:
        """Default diagnostics to [] and accept a result validated from a dict with no diagnostics key."""
        assert BaseResult().diagnostics == []
        assert BaseResult.model_validate({"errors": ["boom"], "error_code": ErrorCode.INTERNAL_ERROR}).diagnostics == []

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


class BaseResultOkTests:
    """Unit tests pinning BaseResult.ok as the absence of an error_code."""

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            pytest.param({}, True, id="success"),
            pytest.param({"errors": ["boom"], "error_code": ErrorCode.GIT_FAILED}, False, id="failed"),
            pytest.param(
                {"warnings": ["careful"], "remediations": [Remediation(message="Retry")]},
                True,
                id="success_with_warnings_and_remediations",
            ),
        ],
    )
    def test_ok_is_true_exactly_when_error_code_is_null(self, fields: dict[str, object], expected: bool) -> None:
        """Return ok == (error_code is None) for success, failed and success-with-warnings results."""
        result = BaseResult.model_validate(fields)

        assert result.ok is expected
        assert (result.error_code is None) is expected
