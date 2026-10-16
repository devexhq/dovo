"""Tier 2 presentation contract tests for AgentProgressFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import AgentProgressEvent
from dovo.cli.ui.formatters.events.agent_progress import AgentProgressFormatter, AgentProgressView
from dovo.core.agents import AgentProgressKind
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

SHELL_ACTION = FormatterCase(
    data=AgentProgressEvent(step_id="plan", kind=AgentProgressKind.SHELL, target="grep -rn needle src"),
    view=AgentProgressView(step_id="plan", kind=AgentProgressKind.SHELL, target="grep -rn needle src", verb="Ran"),
    render_expectations=["grep -rn needle src"],
)

READ_ACTION = FormatterCase(
    data=AgentProgressEvent(step_id="plan", kind=AgentProgressKind.READ, target="README.md"),
    view=AgentProgressView(step_id="plan", kind=AgentProgressKind.READ, target="README.md", verb="Read"),
    render_expectations=["README.md"],
)

EDIT_ACTION = FormatterCase(
    data=AgentProgressEvent(step_id="plan", kind=AgentProgressKind.EDIT, target="src/app.py"),
    view=AgentProgressView(step_id="plan", kind=AgentProgressKind.EDIT, target="src/app.py", verb="Edited"),
    render_expectations=["src/app.py"],
)

SEARCH_ACTION = FormatterCase(
    data=AgentProgressEvent(step_id="plan", kind=AgentProgressKind.SEARCH, target="typer.Typer"),
    view=AgentProgressView(step_id="plan", kind=AgentProgressKind.SEARCH, target="typer.Typer", verb="Searched"),
    render_expectations=["typer.Typer"],
)

NOTE_WITH_INLINE_CODE = FormatterCase(
    data=AgentProgressEvent(step_id="plan", kind=AgentProgressKind.NOTE, target="Step 3: grep for `typer.Typer`."),
    view=AgentProgressView(step_id="plan", kind=AgentProgressKind.NOTE, target="Step 3: grep for `typer.Typer`."),
    render_expectations=["Step 3: grep for typer.Typer."],
)

SHELL_RESULT_WITH_OMITTED_LINES = FormatterCase(
    data=AgentProgressEvent(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="first\nsecond\nthird",
        call_kind=AgentProgressKind.SHELL,
        line_count=9,
    ),
    view=AgentProgressView(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="first\nsecond\nthird",
        call_kind=AgentProgressKind.SHELL,
        line_count=9,
        result_lines=["first", "second", "third"],
        omitted_lines=6,
    ),
    render_expectations=["first", "second", "third", "+6"],
)

READ_RESULT = FormatterCase(
    data=AgentProgressEvent(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="1\t# Title\n2\t",
        call_kind=AgentProgressKind.READ,
        line_count=145,
    ),
    view=AgentProgressView(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="1\t# Title\n2\t",
        call_kind=AgentProgressKind.READ,
        line_count=145,
        result_lines=["Read 145 lines"],
    ),
    render_expectations=["Read 145 lines"],
)

EDIT_RESULT = FormatterCase(
    data=AgentProgressEvent(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="File created successfully\nextra detail",
        call_kind=AgentProgressKind.EDIT,
        line_count=2,
    ),
    view=AgentProgressView(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="File created successfully\nextra detail",
        call_kind=AgentProgressKind.EDIT,
        line_count=2,
        result_lines=["File created successfully"],
    ),
    render_expectations=["File created successfully"],
)

READ_ERROR_RESULT = FormatterCase(
    data=AgentProgressEvent(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="no such file",
        call_kind=AgentProgressKind.READ,
        line_count=1,
        is_error=True,
    ),
    view=AgentProgressView(
        step_id="plan",
        kind=AgentProgressKind.RESULT,
        target="no such file",
        call_kind=AgentProgressKind.READ,
        line_count=1,
        is_error=True,
        result_lines=["no such file"],
    ),
    render_expectations=["no such file"],
)

AGENT_PROGRESS_CASES = [
    pytest.param(SHELL_ACTION, id="shell_action"),
    pytest.param(READ_ACTION, id="read_action"),
    pytest.param(EDIT_ACTION, id="edit_action"),
    pytest.param(SEARCH_ACTION, id="search_action"),
    pytest.param(NOTE_WITH_INLINE_CODE, id="note_with_inline_code"),
    pytest.param(SHELL_RESULT_WITH_OMITTED_LINES, id="shell_result_with_omitted_lines"),
    pytest.param(READ_RESULT, id="read_result"),
    pytest.param(EDIT_RESULT, id="edit_result"),
    pytest.param(READ_ERROR_RESULT, id="read_error_result"),
]

AGENT_PROGRESS_PAYLOAD_CASES = [
    pytest.param(
        SHELL_ACTION,
        {
            "step_id": "plan",
            "kind": "shell",
            "target": "grep -rn needle src",
            "call_kind": None,
            "line_count": 0,
            "is_error": False,
        },
        id="sparse_action",
    ),
    pytest.param(
        READ_ERROR_RESULT,
        {
            "step_id": "plan",
            "kind": "result",
            "target": "no such file",
            "call_kind": "read",
            "line_count": 1,
            "is_error": True,
        },
        id="populated_result",
    ),
]


class AgentProgressFormatterTests:
    """Tier 2 presentation contract tests for AgentProgressFormatter."""

    @pytest.mark.parametrize("case", AGENT_PROGRESS_CASES)
    def test_transform_derives_the_verb_and_result_lines(
        self, case: FormatterCase[AgentProgressEvent, AgentProgressView]
    ) -> None:
        assert AgentProgressFormatter().transform(case.data) == case.view

    @pytest.mark.parametrize(("case", "expected_payload"), AGENT_PROGRESS_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[AgentProgressEvent, AgentProgressView],
        expected_payload: dict[str, Any],
    ) -> None:
        assert_json_payload_matches_published_shape(AgentProgressFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", AGENT_PROGRESS_CASES)
    def test_rich_render_shows_every_view_value(
        self, case: FormatterCase[AgentProgressEvent, AgentProgressView]
    ) -> None:
        assert_rich_render_shows_every_view_value(AgentProgressFormatter, case.data, case.render_expectations)
