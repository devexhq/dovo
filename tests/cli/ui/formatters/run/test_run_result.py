"""Tier 2 presentation contract tests for RunResultFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from dovo.cli.ui.formatters.run.run_result import RunResultFormatter
from dovo.cli.ui.formatters.run.run_views import RunResultView
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.db import SessionStatus
from dovo.engine.models import BlueprintRunResult
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
    render_rich,
)

CANCELLED: Final[str] = "Execution cancelled by user."
RECORD: Final[DiagnosticRecord] = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)
RECORD_LINE: Final[str] = (
    "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)"
)
FIX: Final[Remediation] = Remediation(message="Fix the step and resume.", command="dovo resume sess-1")
SESSION: Final[dict[str, str]] = {"session_id": "sess-1", "blueprint_name": "deploy"}


def _case(render_expectations: list[str], **fields: object) -> FormatterCase[BlueprintRunResult, RunResultView]:
    """Build the case whose view mirrors the result's fields, with ok from error_code."""
    return FormatterCase(
        data=BlueprintRunResult.model_validate(fields),
        view=RunResultView.model_validate({"ok": fields.get("error_code") is None, **fields}),
        render_expectations=render_expectations,
    )


COMPLETED = _case(
    ["Blueprint Run Completed: deploy (session: sess-1, status: completed)"],
    session_status=SessionStatus.COMPLETED,
    **SESSION,
)
HEADLESS_PAUSED = _case(
    ["Blueprint paused; checkpoint saved."],
    session_status=SessionStatus.PAUSED,
    paused_prompt_id="approve",
    **SESSION,
)
CANCELLED_DURING_STEP = _case(
    ["Run Cancelled", CANCELLED],
    error_code=ErrorCode.RUN_CANCELLED,
    errors=[CANCELLED],
    session_status=SessionStatus.CANCELLED,
    **SESSION,
)
INTERRUPTED_AT_PROMPT = _case(
    ["Run Cancelled", "Step 'a' failed: exit 1"],
    error_code=ErrorCode.RUN_CANCELLED,
    errors=["Step 'a' failed: exit 1"],
    session_status=SessionStatus.PAUSED,
    **SESSION,
)
FAILED = _case(
    ["Run Failed", "Step 'a' failed: exit 1", "Cleanup failed.", "Fix:", "Fix the step and resume."],
    error_code=ErrorCode.RUN_STEP_FAILED,
    errors=["Step 'a' failed: exit 1", "Cleanup failed."],
    remediations=[FIX],
    session_status=SessionStatus.FAILED,
    **SESSION,
)
FAILED_WITHOUT_SESSION = _case(
    ["Run Failed", "Catalog item 'nope' not found."],
    error_code=ErrorCode.CATALOG_NOT_FOUND,
    errors=["Catalog item 'nope' not found."],
)

RUN_RESULT_CASES = [
    pytest.param(COMPLETED, id="completed"),
    pytest.param(HEADLESS_PAUSED, id="headless_paused"),
    pytest.param(CANCELLED_DURING_STEP, id="cancelled_during_step"),
    pytest.param(INTERRUPTED_AT_PROMPT, id="interrupted_at_prompt"),
    pytest.param(FAILED, id="failed"),
    pytest.param(FAILED_WITHOUT_SESSION, id="failed_without_session"),
]


def _payload(**overrides: object) -> dict[str, object]:
    """Return the published JSON dict: the header in order, then the session fields."""
    return {
        "ok": True,
        "error_code": None,
        "errors": [],
        "warnings": [],
        "remediations": [],
        "diagnostics": [],
        "session_id": None,
        "blueprint_name": None,
        "session_status": None,
        "paused_prompt_id": None,
        **overrides,
    }


RUN_RESULT_PAYLOAD_CASES = [
    pytest.param(
        COMPLETED,
        _payload(session_id="sess-1", blueprint_name="deploy", session_status="completed"),
        id="completed",
    ),
    pytest.param(
        HEADLESS_PAUSED,
        _payload(session_id="sess-1", blueprint_name="deploy", session_status="paused", paused_prompt_id="approve"),
        id="headless_paused",
    ),
    pytest.param(
        CANCELLED_DURING_STEP,
        _payload(
            ok=False,
            error_code="RUN_CANCELLED",
            errors=[CANCELLED],
            session_id="sess-1",
            blueprint_name="deploy",
            session_status="cancelled",
        ),
        id="cancelled_during_step",
    ),
    pytest.param(
        INTERRUPTED_AT_PROMPT,
        _payload(
            ok=False,
            error_code="RUN_CANCELLED",
            errors=["Step 'a' failed: exit 1"],
            session_id="sess-1",
            blueprint_name="deploy",
            session_status="paused",
        ),
        id="interrupted_at_prompt",
    ),
    pytest.param(
        FAILED,
        _payload(
            ok=False,
            error_code="RUN_STEP_FAILED",
            errors=["Step 'a' failed: exit 1", "Cleanup failed."],
            remediations=[{"message": "Fix the step and resume.", "command": "dovo resume sess-1"}],
            session_id="sess-1",
            blueprint_name="deploy",
            session_status="failed",
        ),
        id="failed",
    ),
    pytest.param(
        FAILED_WITHOUT_SESSION,
        _payload(ok=False, error_code="CATALOG_NOT_FOUND", errors=["Catalog item 'nope' not found."]),
        id="failed_without_session",
    ),
]


class RunResultFormatterTests:
    """Tier 2 presentation contract tests for RunResultFormatter."""

    @pytest.mark.parametrize("case", RUN_RESULT_CASES)
    def test_transform_builds_the_view(self, case: FormatterCase[BlueprintRunResult, RunResultView]) -> None:
        assert_transform_derives_expected_view(RunResultFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), RUN_RESULT_PAYLOAD_CASES)
    def test_json_is_the_header_then_the_session_fields(
        self, case: FormatterCase[BlueprintRunResult, RunResultView], expected_payload: dict[str, object]
    ) -> None:
        assert_json_payload_matches_published_shape(RunResultFormatter, case.data, expected_payload)
        assert list(RunResultFormatter().to_json_serializable(case.data)) == list(expected_payload)

    def test_json_diagnostics_hold_only_event_id_and_log_path(self) -> None:
        result = BlueprintRunResult(
            warnings=["careful"], diagnostics=[RECORD], session_id="sess-1", blueprint_name="deploy"
        )

        payload = RunResultFormatter().to_json_serializable(result)

        assert payload["diagnostics"] == [
            {"event_id": "exc_3f9a1c07b2de", "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"}
        ]
        assert payload["warnings"] == ["careful"]

    @pytest.mark.parametrize("case", RUN_RESULT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[BlueprintRunResult, RunResultView]) -> None:
        assert_rich_render_shows_every_view_value(RunResultFormatter, case.data, case.render_expectations)

    def test_rich_render_orders_warnings_outcome_then_details_lines(self) -> None:
        result = BlueprintRunResult(
            error_code=ErrorCode.RUN_STEP_FAILED,
            errors=["Step 'a' failed: exit 1"],
            warnings=["First warning.", "Second warning."],
            diagnostics=[RECORD],
            session_status=SessionStatus.FAILED,
            session_id="sess-1",
            blueprint_name="deploy",
        )

        rendered = render_rich(RunResultFormatter().to_rich(result))

        positions = [
            rendered.index("Warning: First warning."),
            rendered.index("Warning: Second warning."),
            rendered.index("Run Failed"),
            rendered.index(RECORD_LINE),
        ]
        assert positions == sorted(positions)
        assert rendered.count(RECORD_LINE) == 1
