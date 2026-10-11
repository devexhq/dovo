"""Tier 2 presentation contract tests for SessionShowFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.formatters.session.common import format_session_duration
from dovo.cli.ui.formatters.session.session_show import SessionShowFormatter
from dovo.cli.ui.formatters.session.session_views import SessionShowView, SessionSummaryView
from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.sessions import HistoryShowResult, HistoryShowStatus, StepRunSummary
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _sample_session_record(
    *,
    session_id: str = "sess-12345678",
    blueprint_name: str = "deploy-blueprint",
    status: SessionStatus = SessionStatus.COMPLETED,
    branch_name: str | None = "feature/test",
    started_at: str | None = "2026-08-19 01:00:00",
    completed_at: str | None = "2026-08-19 01:00:10",
    error_message: str | None = None,
) -> SessionRecord:
    return SessionRecord(
        id=1,
        session_id=session_id,
        blueprint_key=blueprint_name,
        blueprint_name=blueprint_name,
        status=status,
        branch_name=branch_name or "",
        started_at=started_at,
        completed_at=completed_at,
        error_message=error_message,
    )


def _make_session_summary_view(**overrides: Any) -> SessionSummaryView:
    defaults: dict[str, Any] = {
        "session_id": "sess-12345678",
        "blueprint_name": "deploy-blueprint",
        "status": "completed",
        "branch_name": "feature/test",
        "started_at": "2026-08-19 01:00:00",
        "completed_at": "2026-08-19 01:00:10",
        "duration_seconds": 10.0,
        "error_message": None,
    }
    defaults.update(overrides)
    return SessionSummaryView(**defaults)


def _make_session_show_view(**overrides: Any) -> SessionShowView:
    defaults: dict[str, Any] = {
        "status": HistoryShowStatus.OK,
        "session_id": "sess-12345678",
        "session": _make_session_summary_view(),
        "log_files": [],
        "log_snippet": [],
        "steps": [],
        "errors": [],
        "warnings": [],
        "fixes": [],
    }
    defaults.update(overrides)
    return SessionShowView(**defaults)


COMPLETED_RUN = FormatterCase(
    data=HistoryShowResult(status=HistoryShowStatus.OK, session_id="sess-12345678", session=_sample_session_record()),
    view=_make_session_show_view(),
    render_expectations=["sess-12345678", "deploy-blueprint", "feature/test", format_session_duration(10.0)],
)

FAILED_RUN_WITH_ERROR = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=_sample_session_record(
            status=SessionStatus.FAILED, error_message="Step 'checkout' failed with exit code 1."
        ),
    ),
    view=_make_session_show_view(
        session=_make_session_summary_view(status="failed", error_message="Step 'checkout' failed with exit code 1.")
    ),
    render_expectations=[
        "sess-12345678",
        "deploy-blueprint",
        "feature/test",
        format_session_duration(10.0),
        "Step 'checkout' failed with exit code 1.",
    ],
)

PAUSED_RUN_WITH_ERROR = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=_sample_session_record(
            status=SessionStatus.PAUSED,
            completed_at=None,
            error_message="Step 'step-2' failed: Waiting for approval",
        ),
    ),
    view=_make_session_show_view(
        session=_make_session_summary_view(
            status="paused",
            completed_at=None,
            duration_seconds=None,
            error_message="Step 'step-2' failed: Waiting for approval",
        ),
    ),
    render_expectations=[
        "sess-12345678",
        "deploy-blueprint",
        "feature/test",
        "Step 'step-2' failed: Waiting for approval",
    ],
)

_LOG_FILES = ["/logs/sess-12345678/01_build_attempt_1.stdout.log", "/logs/sess-12345678/session.log"]
_LOG_SNIPPET = ["[2026-08-19T01:00:01+00:00] step_start step_id=build"]
RUN_WITH_LOGS = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=_sample_session_record(),
        log_files=_LOG_FILES,
        log_snippet=_LOG_SNIPPET,
    ),
    view=_make_session_show_view(log_files=_LOG_FILES, log_snippet=_LOG_SNIPPET),
    render_expectations=["sess-12345678", *_LOG_FILES, *_LOG_SNIPPET],
)

_STEPS = [
    StepRunSummary(
        kind="step", step_id="format", name="Format", state="completed", attempts=1, exit_code=0, duration_seconds=0.04
    ),
    StepRunSummary(
        kind="step",
        step_id="tests",
        state="failed",
        attempts=1,
        exit_code=1,
        duration_seconds=31.9,
        error_message="Command failed with exit code 1.",
    ),
    StepRunSummary(kind="loop", step_id="build-loop", state="completed", iterations=1),
    StepRunSummary(
        kind="step",
        step_id="code",
        state="completed",
        attempts=1,
        exit_code=0,
        duration_seconds=41.0,
        loop_id="build-loop",
        iteration=1,
    ),
    StepRunSummary(kind="step", step_id="review", state="pending", loop_id="build-loop", iteration=1),
]
RUN_WITH_STEPS = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK, session_id="sess-12345678", session=_sample_session_record(), steps=_STEPS
    ),
    view=_make_session_show_view(steps=_STEPS),
    render_expectations=[
        "format",
        "completed",
        "<0.1s",
        "tests",
        "failed",
        "31.9s",
        "• tests: Command failed with exit code 1.",
        "build-loop",
        "code (iter 1)",
        "41.0s",
        "review (iter 1)",
        "pending",
        "Use 'dovo logs sess-12345678 --step <step_id>' for step output.",
    ],
)

_NO_STEP_DATA_WARNING = "No step data recorded for this session."
RUN_WITHOUT_STEP_DATA = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=_sample_session_record(),
        warnings=[_NO_STEP_DATA_WARNING],
    ),
    view=_make_session_show_view(warnings=[_NO_STEP_DATA_WARNING]),
    render_expectations=["sess-12345678", _NO_STEP_DATA_WARNING],
)

NOT_FOUND = FormatterCase(
    data=HistoryShowResult(status=HistoryShowStatus.NOT_FOUND, session_id="nonexistent-sess"),
    view=_make_session_show_view(status=HistoryShowStatus.NOT_FOUND, session_id="nonexistent-sess", session=None),
    render_expectations=["nonexistent-sess"],
)

SHOW_ERROR = FormatterCase(
    data=HistoryShowResult(status=HistoryShowStatus.OK, session_id="sess-1", errors=["Database locked"]),
    view=_make_session_show_view(session_id="sess-1", session=None, errors=["Database locked"]),
    render_expectations=["Database locked"],
)

HISTORY_SHOW_CASES = [
    pytest.param(COMPLETED_RUN, id="completed_run"),
    pytest.param(FAILED_RUN_WITH_ERROR, id="failed_run_with_error"),
    pytest.param(PAUSED_RUN_WITH_ERROR, id="paused_run_with_error"),
    pytest.param(RUN_WITH_LOGS, id="run_with_logs"),
    pytest.param(RUN_WITH_STEPS, id="run_with_steps"),
    pytest.param(RUN_WITHOUT_STEP_DATA, id="run_without_step_data"),
    pytest.param(NOT_FOUND, id="not_found"),
    pytest.param(SHOW_ERROR, id="show_error"),
]

HISTORY_SHOW_PAYLOAD_CASES = [
    pytest.param(
        COMPLETED_RUN,
        {
            "status": "ok",
            "session_id": "sess-12345678",
            "session": {
                "session_id": "sess-12345678",
                "blueprint_name": "deploy-blueprint",
                "status": "completed",
                "branch_name": "feature/test",
                "started_at": "2026-08-19 01:00:00",
                "completed_at": "2026-08-19 01:00:10",
                "duration_seconds": 10.0,
                "error_message": None,
            },
            "log_files": [],
            "log_snippet": [],
            "steps": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="completed_run",
    ),
    pytest.param(
        FAILED_RUN_WITH_ERROR,
        {
            "status": "ok",
            "session_id": "sess-12345678",
            "session": {
                "session_id": "sess-12345678",
                "blueprint_name": "deploy-blueprint",
                "status": "failed",
                "branch_name": "feature/test",
                "started_at": "2026-08-19 01:00:00",
                "completed_at": "2026-08-19 01:00:10",
                "duration_seconds": 10.0,
                "error_message": "Step 'checkout' failed with exit code 1.",
            },
            "log_files": [],
            "log_snippet": [],
            "steps": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="failed_run_with_error",
    ),
    pytest.param(
        PAUSED_RUN_WITH_ERROR,
        {
            "status": "ok",
            "session_id": "sess-12345678",
            "session": {
                "session_id": "sess-12345678",
                "blueprint_name": "deploy-blueprint",
                "status": "paused",
                "branch_name": "feature/test",
                "started_at": "2026-08-19 01:00:00",
                "completed_at": None,
                "duration_seconds": None,
                "error_message": "Step 'step-2' failed: Waiting for approval",
            },
            "log_files": [],
            "log_snippet": [],
            "steps": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="paused_run_with_error",
    ),
    pytest.param(
        RUN_WITH_LOGS,
        {
            "status": "ok",
            "session_id": "sess-12345678",
            "session": {
                "session_id": "sess-12345678",
                "blueprint_name": "deploy-blueprint",
                "status": "completed",
                "branch_name": "feature/test",
                "started_at": "2026-08-19 01:00:00",
                "completed_at": "2026-08-19 01:00:10",
                "duration_seconds": 10.0,
                "error_message": None,
            },
            "log_files": [
                "/logs/sess-12345678/01_build_attempt_1.stdout.log",
                "/logs/sess-12345678/session.log",
            ],
            "log_snippet": ["[2026-08-19T01:00:01+00:00] step_start step_id=build"],
            "steps": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="run_with_logs",
    ),
    pytest.param(
        RUN_WITH_STEPS,
        {
            "status": "ok",
            "session_id": "sess-12345678",
            "session": {
                "session_id": "sess-12345678",
                "blueprint_name": "deploy-blueprint",
                "status": "completed",
                "branch_name": "feature/test",
                "started_at": "2026-08-19 01:00:00",
                "completed_at": "2026-08-19 01:00:10",
                "duration_seconds": 10.0,
                "error_message": None,
            },
            "log_files": [],
            "log_snippet": [],
            "steps": [
                {
                    "kind": "step",
                    "step_id": "format",
                    "name": "Format",
                    "state": "completed",
                    "attempts": 1,
                    "exit_code": 0,
                    "duration_seconds": 0.04,
                    "error_message": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                },
                {
                    "kind": "step",
                    "step_id": "tests",
                    "name": None,
                    "state": "failed",
                    "attempts": 1,
                    "exit_code": 1,
                    "duration_seconds": 31.9,
                    "error_message": "Command failed with exit code 1.",
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                },
                {
                    "kind": "loop",
                    "step_id": "build-loop",
                    "name": None,
                    "state": "completed",
                    "attempts": None,
                    "exit_code": None,
                    "duration_seconds": None,
                    "error_message": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": 1,
                },
                {
                    "kind": "step",
                    "step_id": "code",
                    "name": None,
                    "state": "completed",
                    "attempts": 1,
                    "exit_code": 0,
                    "duration_seconds": 41.0,
                    "error_message": None,
                    "loop_id": "build-loop",
                    "iteration": 1,
                    "iterations": None,
                },
                {
                    "kind": "step",
                    "step_id": "review",
                    "name": None,
                    "state": "pending",
                    "attempts": None,
                    "exit_code": None,
                    "duration_seconds": None,
                    "error_message": None,
                    "loop_id": "build-loop",
                    "iteration": 1,
                    "iterations": None,
                },
            ],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="run_with_steps",
    ),
    pytest.param(
        RUN_WITHOUT_STEP_DATA,
        {
            "status": "ok",
            "session_id": "sess-12345678",
            "session": {
                "session_id": "sess-12345678",
                "blueprint_name": "deploy-blueprint",
                "status": "completed",
                "branch_name": "feature/test",
                "started_at": "2026-08-19 01:00:00",
                "completed_at": "2026-08-19 01:00:10",
                "duration_seconds": 10.0,
                "error_message": None,
            },
            "log_files": [],
            "log_snippet": [],
            "steps": [],
            "errors": [],
            "warnings": ["No step data recorded for this session."],
            "fixes": [],
        },
        id="run_without_step_data",
    ),
    pytest.param(
        NOT_FOUND,
        {
            "status": "not_found",
            "session_id": "nonexistent-sess",
            "session": None,
            "log_files": [],
            "log_snippet": [],
            "steps": [],
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="not_found",
    ),
    pytest.param(
        SHOW_ERROR,
        {
            "status": "ok",
            "session_id": "sess-1",
            "session": None,
            "log_files": [],
            "log_snippet": [],
            "steps": [],
            "errors": ["Database locked"],
            "warnings": [],
            "fixes": [],
        },
        id="show_error",
    ),
]


class SessionShowFormatterTests:
    """Tier 2 presentation contract tests for SessionShowFormatter."""

    @pytest.mark.parametrize("case", HISTORY_SHOW_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[HistoryShowResult, SessionShowView]) -> None:
        """Verify transform derives the expected SessionShowView."""
        assert_transform_derives_expected_view(SessionShowFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), HISTORY_SHOW_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[HistoryShowResult, SessionShowView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(SessionShowFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", HISTORY_SHOW_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[HistoryShowResult, SessionShowView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(SessionShowFormatter, case.data, case.render_expectations)
