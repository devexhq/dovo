"""Tier 2 presentation contracts for DiffResultFormatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from dovo.cli.ui.formatters.diff import DiffResultFormatter, DiffResultView
from dovo.core.diff.models import DiffResult, DiffStatus
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


def make_diff_view(**overrides: Any) -> DiffResultView:
    """Helper to construct a DiffResultView with healthy baseline defaults."""
    defaults: dict[str, Any] = {
        "status": DiffStatus.OK,
        "session_id": "sbx_fmt_1",
        "artifact_path": Path("/repo/.dovo/sessions/sbx_fmt_1/diff.patch"),
        "relative_path": "/repo/.dovo/sessions/sbx_fmt_1/diff.patch",
        "diff_text": _SAMPLE_DIFF,
        "raw": False,
        "full": False,
        "max_lines": None,
        "total_lines": 6,
        "truncated": False,
        "truncated_lines": 0,
        "errors": [],
        "warnings": [],
        "fixes": [],
    }
    defaults.update(overrides)
    return DiffResultView(**defaults)


OK_POPULATED_DATA = DiffResult(
    status=DiffStatus.OK,
    session_id="sbx_fmt_1",
    artifact_path=Path("/repo/.dovo/sessions/sbx_fmt_1/diff.patch"),
    diff_text=_SAMPLE_DIFF,
    warnings=["Non-critical warning"],
)
OK_POPULATED = FormatterCase(
    data=OK_POPULATED_DATA,
    view=make_diff_view(
        status=DiffStatus.OK,
        session_id="sbx_fmt_1",
        artifact_path=Path("/repo/.dovo/sessions/sbx_fmt_1/diff.patch"),
        relative_path="/repo/.dovo/sessions/sbx_fmt_1/diff.patch",
        diff_text=_SAMPLE_DIFF,
        total_lines=6,
        warnings=["Non-critical warning"],
    ),
    render_expectations=[
        "sbx_fmt_1",
        "/repo/.dovo/sessions/sbx_fmt_1/diff.patch",
        *(line.strip() for line in _SAMPLE_DIFF.splitlines()),
    ],
)

EMPTY_DIFF_DATA = DiffResult(
    status=DiffStatus.EMPTY_DIFF,
    session_id="sbx_fmt_empty",
    artifact_path=Path("/repo/.dovo/sessions/sbx_fmt_empty/diff.patch"),
    diff_text="",
)
EMPTY_DIFF = FormatterCase(
    data=EMPTY_DIFF_DATA,
    view=make_diff_view(
        status=DiffStatus.EMPTY_DIFF,
        session_id="sbx_fmt_empty",
        artifact_path=Path("/repo/.dovo/sessions/sbx_fmt_empty/diff.patch"),
        relative_path="/repo/.dovo/sessions/sbx_fmt_empty/diff.patch",
        diff_text="",
        total_lines=0,
    ),
    render_expectations=["sbx_fmt_empty", "No changes recorded"],
)

SESSION_NOT_FOUND_DATA = DiffResult(
    status=DiffStatus.SESSION_NOT_FOUND,
    session_id="sbx_missing_99",
    errors=["Session 'sbx_missing_99' not found under .dovo/sessions/."],
    fixes=["Run `dovo sandbox list` or check .dovo/sessions/ for valid session IDs"],
)
SESSION_NOT_FOUND = FormatterCase(
    data=SESSION_NOT_FOUND_DATA,
    view=make_diff_view(
        status=DiffStatus.SESSION_NOT_FOUND,
        session_id="sbx_missing_99",
        artifact_path=None,
        relative_path=".dovo/sessions/sbx_missing_99/diff.patch",
        diff_text="",
        total_lines=0,
        errors=["Session 'sbx_missing_99' not found under .dovo/sessions/."],
        fixes=["Run `dovo sandbox list` or check .dovo/sessions/ for valid session IDs"],
    ),
    render_expectations=[
        "sbx_missing_99",
        "Session 'sbx_missing_99' not found under .dovo/sessions/.",
        "Run `dovo sandbox list` or check .dovo/sessions/ for valid session IDs",
    ],
)

DIFF_TRUNCATED_TRUE_DATA = DiffResult(
    status=DiffStatus.OK,
    session_id="sbx_truncated_true",
    artifact_path=Path("/repo/.dovo/sessions/sbx_truncated_true/diff.patch"),
    diff_text=_SAMPLE_DIFF,
    max_lines=2,
)
DIFF_WITH_TRUNCATED_TRUE = FormatterCase(
    data=DIFF_TRUNCATED_TRUE_DATA,
    view=make_diff_view(
        status=DiffStatus.OK,
        session_id="sbx_truncated_true",
        artifact_path=Path("/repo/.dovo/sessions/sbx_truncated_true/diff.patch"),
        relative_path="/repo/.dovo/sessions/sbx_truncated_true/diff.patch",
        diff_text=_SAMPLE_DIFF,
        max_lines=2,
        total_lines=6,
        truncated=True,
        truncated_lines=2,
    ),
    render_expectations=[
        "sbx_truncated_true",
        "/repo/.dovo/sessions/sbx_truncated_true/diff.patch",
        "2",
        "6",
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
            "session_id": "sbx_fmt_1",
            "artifact_path": "/repo/.dovo/sessions/sbx_fmt_1/diff.patch",
            "relative_path": "/repo/.dovo/sessions/sbx_fmt_1/diff.patch",
            "diff_text": _SAMPLE_DIFF,
            "raw": False,
            "full": False,
            "max_lines": None,
            "total_lines": 6,
            "truncated": False,
            "truncated_lines": 0,
            "errors": [],
            "warnings": ["Non-critical warning"],
            "fixes": [],
        },
        id="ok_populated_diff",
    ),
    pytest.param(
        EMPTY_DIFF,
        {
            "status": "empty_diff",
            "session_id": "sbx_fmt_empty",
            "artifact_path": "/repo/.dovo/sessions/sbx_fmt_empty/diff.patch",
            "relative_path": "/repo/.dovo/sessions/sbx_fmt_empty/diff.patch",
            "diff_text": "",
            "raw": False,
            "full": False,
            "max_lines": None,
            "total_lines": 0,
            "truncated": False,
            "truncated_lines": 0,
            "errors": [],
            "warnings": [],
            "fixes": [],
        },
        id="empty_diff",
    ),
    pytest.param(
        SESSION_NOT_FOUND,
        {
            "status": "session_not_found",
            "session_id": "sbx_missing_99",
            "artifact_path": None,
            "relative_path": ".dovo/sessions/sbx_missing_99/diff.patch",
            "diff_text": "",
            "raw": False,
            "full": False,
            "max_lines": None,
            "total_lines": 0,
            "truncated": False,
            "truncated_lines": 0,
            "errors": ["Session 'sbx_missing_99' not found under .dovo/sessions/."],
            "warnings": [],
            "fixes": ["Run `dovo sandbox list` or check .dovo/sessions/ for valid session IDs"],
        },
        id="session_not_found",
    ),
]

TO_RAW_CASES = [
    pytest.param(
        DiffResultFormatter(),
        DiffResult(
            status=DiffStatus.OK,
            session_id="sbx_raw_1",
            artifact_path=Path("/repo/.dovo/sessions/sbx_raw_1/diff.patch"),
            diff_text="diff --git a/a.txt b/a.txt\n+line\n",
            raw=True,
        ),
        "diff --git a/a.txt b/a.txt\n+line\n",
        id="raw_flag_returns_exact_diff_text",
    ),
    pytest.param(
        DiffResultFormatter(),
        DiffResult(
            status=DiffStatus.EMPTY_DIFF,
            session_id="sbx_raw_empty",
            artifact_path=Path("/repo/.dovo/sessions/sbx_raw_empty/diff.patch"),
            diff_text="",
        ),
        "No changes recorded for session sbx_raw_empty.",
        id="empty_diff_returns_notice",
    ),
    pytest.param(
        DiffResultFormatter(),
        DiffResult(
            status=DiffStatus.SESSION_NOT_FOUND,
            session_id="sbx_missing_raw",
            errors=["Session 'sbx_missing_raw' not found under .dovo/sessions/."],
        ),
        "Session 'sbx_missing_raw' not found",
        id="session_not_found_renders_error_panel_text",
    ),
    pytest.param(
        DiffResultFormatter(console=Console(force_terminal=True), max_lines=2),
        DiffResult(
            status=DiffStatus.OK,
            session_id="sbx_trunc",
            artifact_path=Path("/repo/.dovo/sessions/sbx_trunc/diff.patch"),
            diff_text="line 1\nline 2\nline 3\nline 4\n",
            raw=False,
        ),
        "line 1\nline 2",
        id="terminal_truncates_at_max_lines",
    ),
    pytest.param(
        DiffResultFormatter(console=Console(force_terminal=True), max_lines=2, full=True),
        DiffResult(
            status=DiffStatus.OK,
            session_id="sbx_trunc",
            artifact_path=Path("/repo/.dovo/sessions/sbx_trunc/diff.patch"),
            diff_text="line 1\nline 2\nline 3\nline 4\n",
            raw=False,
        ),
        "line 1\nline 2\nline 3\nline 4\n",
        id="full_flag_bypasses_truncation",
    ),
    pytest.param(
        DiffResultFormatter(console=Console(force_terminal=False), max_lines=2),
        DiffResult(
            status=DiffStatus.OK,
            session_id="sbx_trunc",
            artifact_path=Path("/repo/.dovo/sessions/sbx_trunc/diff.patch"),
            diff_text="line 1\nline 2\nline 3\nline 4\n",
            raw=False,
        ),
        "line 1\nline 2\nline 3\nline 4\n",
        id="non_terminal_bypasses_truncation",
    ),
]

_TERMINAL_FORMATTER = DiffResultFormatter(console=Console(force_terminal=True))


class DiffResultFormatterTests:
    """Tier 2 presentation contract tests for DiffResultFormatter."""

    @pytest.mark.parametrize("case", DIFF_CASES)
    def test_transform_derives_expected_view(self, case: FormatterCase[DiffResult, DiffResultView]) -> None:
        """Verify transform derives the exact DiffResultView model representation."""
        assert_transform_derives_expected_view(_TERMINAL_FORMATTER, case.data, case.view)

    @pytest.mark.parametrize(("case", "expected_payload"), DIFF_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[DiffResult, DiffResultView],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the exact published wire-format literal dict."""
        assert_json_payload_matches_published_shape(DiffResultFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", DIFF_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[DiffResult, DiffResultView]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(_TERMINAL_FORMATTER, case.data, case.render_expectations)

    @pytest.mark.parametrize(("formatter", "result", "expected"), TO_RAW_CASES)
    def test_to_raw_renders_expected_output(
        self,
        formatter: DiffResultFormatter,
        result: DiffResult,
        expected: str,
    ) -> None:
        """Verify to_raw renders expected raw diff output, notice, error panel, or truncation."""
        raw_output = formatter.to_raw(result)
        assert expected in raw_output
