"""Tier 2 presentation contract tests for PromptFormatter."""

from __future__ import annotations

from typing import Any

import pytest

from dovo.cli.ui.events import PromptEvent, PromptEventArtifact, PromptEventComment, PromptOption
from dovo.cli.ui.formatters.events.prompt import PromptFormatter
from tests.harness.formatter import (
    FormatterCase,
    assert_json_payload_matches_published_shape,
    assert_rich_render_shows_every_view_value,
)

_CONTINUE_OPTION = PromptOption(key="c", label="Continue", decision="continue")
_ABORT_OPTION = PromptOption(key="a", label="Abort", decision="abort")

WITH_DIAGNOSTIC = FormatterCase(
    data=PromptEvent(
        prompt_type="step_failure",
        step_id="step-1",
        title="Action Needed",
        diagnostic="Step timed out",
        options=[_CONTINUE_OPTION],
        default="c",
    ),
    view=PromptEvent(
        prompt_type="step_failure",
        step_id="step-1",
        title="Action Needed",
        diagnostic="Step timed out",
        options=[_CONTINUE_OPTION],
        default="c",
    ),
    render_expectations=["Action Needed", "Step timed out"],
)

WITHOUT_DIAGNOSTIC = FormatterCase(
    data=PromptEvent(
        prompt_type="step_failure",
        step_id="step-2",
        title="Blueprint Prompt",
        diagnostic=None,
        options=[_ABORT_OPTION],
    ),
    view=PromptEvent(
        prompt_type="step_failure",
        step_id="step-2",
        title="Blueprint Prompt",
        diagnostic=None,
        options=[_ABORT_OPTION],
    ),
    render_expectations=["Blueprint Prompt"],
)

_INTERACTION_FULL = PromptEvent(
    prompt_type="interaction",
    step_id="approve",
    title="Approve this plan?",
    default=None,
    mode="select",
    message="Approve this plan?",
    artifact=PromptEventArtifact(
        name="plan",
        revision=2,
        digest="b07d44aa",
        reference="plan@2  sha256:b07d44aa…",
        preview=["Step one", "Step two"],
    ),
    options=[
        PromptOption(key="1", label="Approve", decision="approve", comment="disabled", fail=False),
        PromptOption(key="2", label="Request changes", decision="revise", comment="required", fail=False),
    ],
    comment=PromptEventComment(policy="optional", message="Instructions for the agent"),
)
INTERACTION_FULL = FormatterCase(
    data=_INTERACTION_FULL,
    view=_INTERACTION_FULL,
    render_expectations=[
        "approve",
        "Approve this plan?",
        "plan",
        "2",
        "b07d44aa",
        "Step one",
        "Step two",
        "Request changes",
    ],
)

_INTERACTION_SPARSE = PromptEvent(
    prompt_type="interaction", step_id="why", title="Why?", default=None, mode="text", message="Why?", options=[]
)
INTERACTION_SPARSE = FormatterCase(
    data=_INTERACTION_SPARSE, view=_INTERACTION_SPARSE, render_expectations=["why", "Why?"]
)

_INTERACTION_MULTISELECT = PromptEvent(
    prompt_type="interaction",
    step_id="triage",
    title="Which findings should the agent fix?",
    default=None,
    mode="multiselect",
    message="Which findings should the agent fix?",
    options=[
        PromptOption(
            key="1",
            label="Missing validation",
            decision="B1",
            comment="optional",
            fail=False,
            display="B1 — Missing validation",
        )
    ],
)
INTERACTION_MULTISELECT = FormatterCase(
    data=_INTERACTION_MULTISELECT,
    view=_INTERACTION_MULTISELECT,
    render_expectations=["triage", "B1", "Missing validation"],
)

PROMPT_CASES = [
    pytest.param(WITH_DIAGNOSTIC, id="with_diagnostic"),
    pytest.param(WITHOUT_DIAGNOSTIC, id="without_diagnostic"),
    pytest.param(INTERACTION_FULL, id="interaction_full"),
    pytest.param(INTERACTION_SPARSE, id="interaction_sparse"),
    pytest.param(INTERACTION_MULTISELECT, id="interaction_multiselect"),
]

PROMPT_PAYLOAD_CASES = [
    pytest.param(
        WITH_DIAGNOSTIC,
        {
            "prompt_type": "step_failure",
            "step_id": "step-1",
            "title": "Action Needed",
            "diagnostic": "Step timed out",
            "options": [{"key": "c", "label": "Continue", "decision": "continue"}],
            "default": "c",
        },
        id="with_diagnostic",
    ),
    pytest.param(
        WITHOUT_DIAGNOSTIC,
        {
            "prompt_type": "step_failure",
            "step_id": "step-2",
            "title": "Blueprint Prompt",
            "diagnostic": None,
            "options": [{"key": "a", "label": "Abort", "decision": "abort"}],
            "default": "abort",
        },
        id="without_diagnostic",
    ),
    pytest.param(
        INTERACTION_FULL,
        {
            "prompt_type": "interaction",
            "step_id": "approve",
            "title": "Approve this plan?",
            "diagnostic": None,
            "options": [
                {"key": "1", "label": "Approve", "decision": "approve", "comment": "disabled", "fail": False},
                {"key": "2", "label": "Request changes", "decision": "revise", "comment": "required", "fail": False},
            ],
            "default": None,
            "mode": "select",
            "message": "Approve this plan?",
            "artifact": {
                "name": "plan",
                "revision": 2,
                "digest": "b07d44aa",
                "reference": "plan@2  sha256:b07d44aa…",
                "preview": ["Step one", "Step two"],
            },
            "comment": {"policy": "optional", "message": "Instructions for the agent"},
        },
        id="interaction_full",
    ),
    pytest.param(
        INTERACTION_SPARSE,
        {
            "prompt_type": "interaction",
            "step_id": "why",
            "title": "Why?",
            "diagnostic": None,
            "options": [],
            "default": None,
            "mode": "text",
            "message": "Why?",
        },
        id="interaction_sparse",
    ),
]


class PromptFormatterTests:
    """Tier 2 presentation contract tests for PromptFormatter."""

    @pytest.mark.parametrize(("case", "expected_payload"), PROMPT_PAYLOAD_CASES)
    def test_json_payload_matches_published_shape(
        self,
        case: FormatterCase[PromptEvent, PromptEvent],
        expected_payload: dict[str, Any],
    ) -> None:
        """Verify to_json_serializable matches the published wire format dict."""
        assert_json_payload_matches_published_shape(PromptFormatter, case.data, expected_payload)

    @pytest.mark.parametrize("case", PROMPT_CASES)
    def test_rich_render_shows_every_view_value(self, case: FormatterCase[PromptEvent, PromptEvent]) -> None:
        """Verify that all non-null semantic view model values reach the Rich renderable output."""
        assert_rich_render_shows_every_view_value(PromptFormatter, case.data, case.render_expectations)
