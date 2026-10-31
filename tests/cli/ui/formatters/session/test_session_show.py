"""Tier 2 presentation contract tests for SessionShowFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dovo.cli.ui.formatters.session.common import format_session_duration
from dovo.cli.ui.formatters.session.session_show import SessionShowFormatter
from dovo.cli.ui.formatters.session.session_views import SessionShowView
from dovo.common.diagnostic_log import DiagnosticLevel, DiagnosticRecord
from dovo.core.db import SessionStatus
from dovo.core.sessions import HistoryShowResult, HistoryShowStatus, StepRunSummary
from tests.harness.builders.sessions import session_record, session_summary_view
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)


def _make_session_show_view(**overrides: object) -> SessionShowView:
    defaults: dict[str, object] = {
        "status": HistoryShowStatus.OK,
        "session_id": "sess-12345678",
        "session": session_summary_view(),
        "log_files": [],
        "log_snippet": [],
        "steps": [],
        "errors": [],
        "warnings": [],
        "remediations": [],
        "diagnostics": [],
    }
    return SessionShowView.model_validate({**defaults, **overrides})


COMPLETED_RUN = FormatterCase(
    data=HistoryShowResult(status=HistoryShowStatus.OK, session_id="sess-12345678", session=session_record()),
    view=_make_session_show_view(),
    render_expectations=["sess-12345678", "deploy-blueprint", "feature/test", format_session_duration(10.0)],
)

KEPT_WORKTREE_RUN = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=session_record(status=SessionStatus.FAILED),
        worktree_kept=True,
        worktree_path=Path.cwd() / ".dovo" / "worktrees" / "sess-12345678",
    ),
    view=_make_session_show_view(
        session=session_summary_view(status="failed"),
        worktree_kept=True,
        worktree_path=".dovo/worktrees/sess-12345678",
    ),
    render_expectations=["sess-12345678", ".dovo/worktrees/sess-12345678"],
)

FAILED_RUN_WITH_ERROR = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=session_record(status=SessionStatus.FAILED, error_message="Step 'checkout' failed with exit code 1."),
    ),
    view=_make_session_show_view(
        session=session_summary_view(status="failed", error_message="Step 'checkout' failed with exit code 1.")
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
        session=session_record(
            status=SessionStatus.PAUSED,
            completed_at=None,
            error_message="Step 'step-2' failed: Waiting for approval",
        ),
    ),
    view=_make_session_show_view(
        session=session_summary_view(
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
        session=session_record(),
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
        status=HistoryShowStatus.OK, session_id="sess-12345678", session=session_record(), steps=_STEPS
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

_GUARDED_STEPS = [
    StepRunSummary(kind="step", step_id="fold", state="skipped", attempts=0, skip_cause="when_false"),
    StepRunSummary(kind="if", step_id="route", state="completed", branch="else"),
    StepRunSummary(
        kind="step", step_id="ship", state="completed", attempts=1, exit_code=0, duration_seconds=1.0, if_id="route"
    ),
]
RUN_WITH_GUARDED_STEPS = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK, session_id="sess-12345678", session=session_record(), steps=_GUARDED_STEPS
    ),
    view=_make_session_show_view(steps=_GUARDED_STEPS),
    render_expectations=["fold", "skipped (when_false)", "route", "completed (else)", "ship"],
)

_NO_STEP_DATA_WARNING = "No step data recorded for this session."
RUN_WITHOUT_STEP_DATA = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=session_record(),
        warnings=[_NO_STEP_DATA_WARNING],
    ),
    view=_make_session_show_view(warnings=[_NO_STEP_DATA_WARNING]),
    render_expectations=["sess-12345678", _NO_STEP_DATA_WARNING],
)

_STEP_READ_WARNING = "Unexpected error during engine.session_show: boom"
_STEP_READ_RECORD = DiagnosticRecord(
    event_id="exc_3f9a1c07b2de",
    log_path=Path("/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json"),
    level=DiagnosticLevel.ERROR,
)
RUN_WITH_STEP_READ_FAILURE = FormatterCase(
    data=HistoryShowResult(
        status=HistoryShowStatus.OK,
        session_id="sess-12345678",
        session=session_record(),
        warnings=[_STEP_READ_WARNING],
        diagnostics=[_STEP_READ_RECORD],
    ),
    view=_make_session_show_view(warnings=[_STEP_READ_WARNING], diagnostics=[_STEP_READ_RECORD]),
    render_expectations=[
        "sess-12345678",
        _STEP_READ_WARNING,
        "Details logged: /home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json (event exc_3f9a1c07b2de)",
    ],
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
    pytest.param(KEPT_WORKTREE_RUN, id="kept_worktree_run"),
    pytest.param(FAILED_RUN_WITH_ERROR, id="failed_run_with_error"),
    pytest.param(PAUSED_RUN_WITH_ERROR, id="paused_run_with_error"),
    pytest.param(RUN_WITH_LOGS, id="run_with_logs"),
    pytest.param(RUN_WITH_STEPS, id="run_with_steps"),
    pytest.param(RUN_WITH_GUARDED_STEPS, id="run_with_guarded_steps"),
    pytest.param(RUN_WITHOUT_STEP_DATA, id="run_without_step_data"),
    pytest.param(RUN_WITH_STEP_READ_FAILURE, id="run_with_step_read_failure"),
    pytest.param(NOT_FOUND, id="not_found"),
    pytest.param(SHOW_ERROR, id="show_error"),
]

HISTORY_SHOW_PAYLOAD_CASES = [
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
                    "rejected_patch_path": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                    "skip_cause": None,
                    "branch": None,
                    "if_id": None,
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
                    "rejected_patch_path": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                    "skip_cause": None,
                    "branch": None,
                    "if_id": None,
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
                    "rejected_patch_path": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": 1,
                    "skip_cause": None,
                    "branch": None,
                    "if_id": None,
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
                    "rejected_patch_path": None,
                    "loop_id": "build-loop",
                    "iteration": 1,
                    "iterations": None,
                    "skip_cause": None,
                    "branch": None,
                    "if_id": None,
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
                    "rejected_patch_path": None,
                    "loop_id": "build-loop",
                    "iteration": 1,
                    "iterations": None,
                    "skip_cause": None,
                    "branch": None,
                    "if_id": None,
                },
            ],
            "worktree_kept": None,
            "worktree_path": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="run_with_steps",
    ),
    pytest.param(
        RUN_WITH_GUARDED_STEPS,
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
                    "step_id": "fold",
                    "name": None,
                    "state": "skipped",
                    "attempts": 0,
                    "exit_code": None,
                    "duration_seconds": None,
                    "error_message": None,
                    "rejected_patch_path": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                    "skip_cause": "when_false",
                    "branch": None,
                    "if_id": None,
                },
                {
                    "kind": "if",
                    "step_id": "route",
                    "name": None,
                    "state": "completed",
                    "attempts": None,
                    "exit_code": None,
                    "duration_seconds": None,
                    "error_message": None,
                    "rejected_patch_path": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                    "skip_cause": None,
                    "branch": "else",
                    "if_id": None,
                },
                {
                    "kind": "step",
                    "step_id": "ship",
                    "name": None,
                    "state": "completed",
                    "attempts": 1,
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                    "error_message": None,
                    "rejected_patch_path": None,
                    "loop_id": None,
                    "iteration": None,
                    "iterations": None,
                    "skip_cause": None,
                    "branch": None,
                    "if_id": "route",
                },
            ],
            "worktree_kept": None,
            "worktree_path": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="run_with_guarded_steps",
    ),
    pytest.param(
        RUN_WITH_STEP_READ_FAILURE,
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
            "worktree_kept": None,
            "worktree_path": None,
            "errors": [],
            "warnings": ["Unexpected error during engine.session_show: boom"],
            "remediations": [],
            "diagnostics": [
                {
                    "event_id": "exc_3f9a1c07b2de",
                    "log_path": "/home/me/.dovo/logs/2026-10-23/exc_3f9a1c07b2de.json",
                }
            ],
        },
        id="run_with_step_read_failure",
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
            "worktree_kept": None,
            "worktree_path": None,
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
        },
        id="not_found",
    ),
]


class SessionShowFormatterTests:
    """Tier 2 presentation contract tests for SessionShowFormatter."""

    @pytest.mark.parametrize("case", HISTORY_SHOW_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[HistoryShowResult, SessionShowView]) -> None:
        assert_transform_derives_expected_view(SessionShowFormatter, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), HISTORY_SHOW_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[HistoryShowResult, SessionShowView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(SessionShowFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", HISTORY_SHOW_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[HistoryShowResult, SessionShowView]) -> None:
        assert_rich_render_shows_every_view_value(SessionShowFormatter, case.data, case.render_expectations)
