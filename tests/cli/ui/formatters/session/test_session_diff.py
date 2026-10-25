"""Tier 2 presentation contracts for SessionDiffFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from dovo.cli.ui.formatters.session import SessionDiffFormatter, SessionDiffView
from dovo.common.models import Remediation
from dovo.core.sessions import DiffResult, DiffStatus
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
    assert_transform_derives_expected_view,
)

_SAMPLE_DIFF = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,3 @@
-def old(): pass
+def new(): pass  # intentionally long line exceeding 120 characters to ensure diff raw output does not wrap at terminal columns boundaries
"""


def make_diff_view(**overrides: object) -> SessionDiffView:
    """Construct a SessionDiffView with healthy baseline defaults."""
    defaults: dict[str, object] = {
        "status": DiffStatus.OK,
        "session_id": "dovo_fmt_1",
        "artifact_path": Path("/repo/.dovo/sessions/dovo_fmt_1/diff.patch"),
        "relative_path": "/repo/.dovo/sessions/dovo_fmt_1/diff.patch",
        "diff_text": _SAMPLE_DIFF,
        "full": False,
        "max_lines": None,
        "total_lines": 6,
        "truncated": False,
        "truncated_lines": 0,
        "errors": [],
        "warnings": [],
        "remediations": [],
    }
    return SessionDiffView.model_validate({**defaults, **overrides})


OK_POPULATED_DATA = DiffResult(
    status=DiffStatus.OK,
    session_id="dovo_fmt_1",
    artifact_path=Path("/repo/.dovo/sessions/dovo_fmt_1/diff.patch"),
    diff_text=_SAMPLE_DIFF,
    warnings=["Non-critical warning"],
)
OK_POPULATED = FormatterCase(
    data=OK_POPULATED_DATA,
    view=make_diff_view(
        status=DiffStatus.OK,
        session_id="dovo_fmt_1",
        artifact_path=Path("/repo/.dovo/sessions/dovo_fmt_1/diff.patch"),
        relative_path="/repo/.dovo/sessions/dovo_fmt_1/diff.patch",
        diff_text=_SAMPLE_DIFF,
        total_lines=6,
        warnings=["Non-critical warning"],
    ),
    render_expectations=[
        "dovo_fmt_1",
        "/repo/.dovo/sessions/dovo_fmt_1/diff.patch",
        *(line.strip() for line in _SAMPLE_DIFF.splitlines()),
    ],
)

EMPTY_DIFF_DATA = DiffResult(
    status=DiffStatus.EMPTY_DIFF,
    session_id="dovo_fmt_empty",
    artifact_path=Path("/repo/.dovo/sessions/dovo_fmt_empty/diff.patch"),
    diff_text="",
)
EMPTY_DIFF = FormatterCase(
    data=EMPTY_DIFF_DATA,
    view=make_diff_view(
        status=DiffStatus.EMPTY_DIFF,
        session_id="dovo_fmt_empty",
        artifact_path=Path("/repo/.dovo/sessions/dovo_fmt_empty/diff.patch"),
        relative_path="/repo/.dovo/sessions/dovo_fmt_empty/diff.patch",
        diff_text="",
        total_lines=0,
    ),
    render_expectations=["dovo_fmt_empty", "No changes recorded"],
)

SESSION_NOT_FOUND_DATA = DiffResult(
    status=DiffStatus.SESSION_NOT_FOUND,
    session_id="dovo_missing_99",
    errors=["Session 'dovo_missing_99' not found."],
    remediations=[
        Remediation(message="Run 'dovo session list' to list valid session IDs", command="dovo session list")
    ],
)
SESSION_NOT_FOUND = FormatterCase(
    data=SESSION_NOT_FOUND_DATA,
    view=make_diff_view(
        status=DiffStatus.SESSION_NOT_FOUND,
        session_id="dovo_missing_99",
        artifact_path=None,
        relative_path="<sessions_dir>/dovo_missing_99/diff.patch",
        diff_text="",
        total_lines=0,
        errors=["Session 'dovo_missing_99' not found."],
        remediations=[
            Remediation(message="Run 'dovo session list' to list valid session IDs", command="dovo session list")
        ],
    ),
    render_expectations=[
        "dovo_missing_99",
        "Session 'dovo_missing_99' not found.",
        "Fix:",
        "- Run 'dovo session list' to list valid session IDs",
    ],
)

DIFF_TRUNCATED_TRUE_DATA = DiffResult(
    status=DiffStatus.OK,
    session_id="dovo_truncated_true",
    artifact_path=Path("/repo/.dovo/sessions/dovo_truncated_true/diff.patch"),
    diff_text=_SAMPLE_DIFF,
    max_lines=2,
)
DIFF_WITH_TRUNCATED_TRUE = FormatterCase(
    data=DIFF_TRUNCATED_TRUE_DATA,
    view=make_diff_view(
        status=DiffStatus.OK,
        session_id="dovo_truncated_true",
        artifact_path=Path("/repo/.dovo/sessions/dovo_truncated_true/diff.patch"),
        relative_path="/repo/.dovo/sessions/dovo_truncated_true/diff.patch",
        diff_text=_SAMPLE_DIFF,
        max_lines=2,
        total_lines=6,
        truncated=True,
        truncated_lines=2,
    ),
    render_expectations=[
        "dovo_truncated_true",
        "/repo/.dovo/sessions/dovo_truncated_true/diff.patch",
        "2",
        "6",
        "dovo diff dovo_truncated_true --patch",
    ],
)

DIFF_CASES = [
    pytest.param(OK_POPULATED, id="ok_populated_diff"),
    pytest.param(EMPTY_DIFF, id="empty_diff"),
    pytest.param(SESSION_NOT_FOUND, id="session_not_found"),
    pytest.param(DIFF_WITH_TRUNCATED_TRUE, id="diff_with_truncated_true"),
]

DIFF_PAYLOAD_CASES = [
    pytest.param(
        OK_POPULATED,
        {
            "status": "ok",
            "session_id": "dovo_fmt_1",
            "artifact_path": "/repo/.dovo/sessions/dovo_fmt_1/diff.patch",
            "relative_path": "/repo/.dovo/sessions/dovo_fmt_1/diff.patch",
            "diff_text": _SAMPLE_DIFF,
            "full": False,
            "max_lines": None,
            "total_lines": 6,
            "truncated": False,
            "truncated_lines": 0,
            "errors": [],
            "warnings": ["Non-critical warning"],
            "remediations": [],
        },
        id="ok_populated_diff",
    ),
    pytest.param(
        SESSION_NOT_FOUND,
        {
            "status": "session_not_found",
            "session_id": "dovo_missing_99",
            "artifact_path": None,
            "relative_path": "<sessions_dir>/dovo_missing_99/diff.patch",
            "diff_text": "",
            "full": False,
            "max_lines": None,
            "total_lines": 0,
            "truncated": False,
            "truncated_lines": 0,
            "errors": ["Session 'dovo_missing_99' not found."],
            "warnings": [],
            "remediations": [
                {"message": "Run 'dovo session list' to list valid session IDs", "command": "dovo session list"}
            ],
        },
        id="session_not_found",
    ),
]

_TERMINAL_FORMATTER = SessionDiffFormatter(console=Console(force_terminal=True))


class SessionDiffFormatterTests:
    """Tier 2 presentation contract tests for SessionDiffFormatter."""

    @pytest.mark.parametrize("case", DIFF_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[DiffResult, SessionDiffView]) -> None:
        assert_transform_derives_expected_view(_TERMINAL_FORMATTER, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), DIFF_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[DiffResult, SessionDiffView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(SessionDiffFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", DIFF_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[DiffResult, SessionDiffView]) -> None:
        assert_rich_render_shows_every_view_value(_TERMINAL_FORMATTER, case.data, case.render_expectations)
