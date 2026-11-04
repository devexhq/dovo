"""Tier 1 tests for ResultHeaderView and header_fields: the header every command-result payload starts with."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from pydantic import ValidationError

from dovo.cli.ui.formatters.header import HEADER_FIELDS, ResultHeaderView, header_fields
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import BaseResult, Remediation

RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)
REMEDIATION: Final[Remediation] = Remediation(message="Run 'dovo init' first.", command="dovo init")


class _NamedView(ResultHeaderView):
    """A command view with one field of its own."""

    name: str


class ResultHeaderViewTests:
    """ResultHeaderView leads every subclass dump and refuses a header that disagrees with error_code."""

    def test_dump_starts_with_the_header_in_order(self) -> None:
        """Dump the six header keys first, in the bound order, then the subclass's own key."""
        payload = _NamedView(ok=True, name="wt-1").model_dump(mode="json")

        assert list(payload) == ["ok", "error_code", "errors", "warnings", "remediations", "diagnostics", "name"]
        assert HEADER_FIELDS == ("ok", "error_code", "errors", "warnings", "remediations", "diagnostics")
        assert payload == {
            "ok": True,
            "error_code": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "name": "wt-1",
        }

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"ok": True, "error_code": ErrorCode.GIT_FAILED, "errors": ["boom"]}, id="ok_with_code"),
            pytest.param({"ok": False}, id="not_ok_without_code"),
        ],
    )
    def test_refuses_ok_that_disagrees_with_error_code(self, fields: dict[str, object]) -> None:
        """Refuse ok true with a code, and ok false without one."""
        with pytest.raises(ValidationError, match="ok must be true exactly when error_code is None"):
            ResultHeaderView.model_validate(fields)

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"ok": True, "errors": ["boom"]}, id="errors_without_code"),
            pytest.param({"ok": False, "error_code": ErrorCode.GIT_FAILED}, id="code_without_errors"),
        ],
    )
    def test_refuses_errors_that_disagree_with_error_code(self, fields: dict[str, object]) -> None:
        """Refuse errors without a code, and a code without errors."""
        with pytest.raises(ValidationError, match="error_code must be set exactly when errors is non-empty"):
            ResultHeaderView.model_validate(fields)

    @pytest.mark.parametrize("field_name", ["errors", "warnings", "remediations", "diagnostics"])
    def test_list_fields_refuse_null(self, field_name: str) -> None:
        """Refuse None for each list field of the header."""
        with pytest.raises(ValidationError) as caught:
            ResultHeaderView.model_validate({"ok": True, field_name: None})

        assert [error["loc"] for error in caught.value.errors()] == [(field_name,)]

    def test_remediation_and_diagnostic_dump_shape(self) -> None:
        """Dump a remediation as {message, command} and a diagnostic as {event_id, log_path}."""
        view = ResultHeaderView(ok=True, warnings=["careful"], remediations=[REMEDIATION], diagnostics=[RECORD])

        payload = view.model_dump(mode="json")

        assert payload["remediations"] == [{"message": "Run 'dovo init' first.", "command": "dovo init"}]
        assert payload["diagnostics"] == [
            {"event_id": "exc_3f9a1c07b2de", "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"}
        ]


class HeaderFieldsTests:
    """header_fields copies a result's header, with ok taken from error_code."""

    def test_copies_the_result_header(self) -> None:
        """Copy a failed result's code, errors, warnings, remediations and diagnostics, with ok false."""
        result = BaseResult(
            error_code=ErrorCode.GIT_FAILED,
            errors=["git failed"],
            warnings=["careful"],
            remediations=[REMEDIATION],
            diagnostics=[RECORD],
        )

        assert header_fields(result) == {
            "ok": False,
            "error_code": ErrorCode.GIT_FAILED,
            "errors": ["git failed"],
            "warnings": ["careful"],
            "remediations": [REMEDIATION],
            "diagnostics": [RECORD],
        }
