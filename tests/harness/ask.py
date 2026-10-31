# tests/harness/ask.py
"""Worked `type: ask` blueprint documents shared by validation and end-to-end tests."""

from __future__ import annotations

_FEATURE_INPUT: dict[str, object] = {"feature": {"type": "string", "description": "Feature to build", "required": True}}


def _blueprint(name: str, steps: list[dict[str, object]]) -> dict[str, object]:
    """Wrap steps in a blueprint document with a required `feature` input."""
    return {
        "version": "1.0",
        "name": name,
        "id": name,
        "description": f"Worked ask blueprint '{name}'.",
        "inputs": _FEATURE_INPUT,
        "steps": steps,
    }


def approval_gate_blueprint() -> dict[str, object]:
    """Return the issue's approval-gate blueprint document (plan, approve, implement)."""
    return _blueprint(
        "approval-gate",
        [
            {
                "id": "plan",
                "type": "agent",
                "prompt": "Write an implementation plan for: ${{ inputs.feature }} to PLAN.md.",
                "tools": {"allow": [{"capability": "read"}, {"capability": "write"}]},
                "artifacts": [{"name": "plan", "path": "PLAN.md"}],
            },
            {
                "id": "approve",
                "type": "ask",
                "interaction": {
                    "mode": "confirm",
                    "message": "Approve this plan?",
                    "artifact": "plan",
                    "comment": {"policy": "optional", "message": "Instructions for the implementer"},
                },
            },
            {
                "id": "implement",
                "type": "agent",
                "prompt": (
                    "Implement the approved plan:\n"
                    "{{ artifacts.plan.content }}\n"
                    "Instructions: {{ steps.approve.outputs.comment }}\n"
                ),
            },
        ],
    )


def revise_loop_blueprint() -> dict[str, object]:
    """Return the issue's revise-until-approved blueprint document."""
    return _blueprint(
        "revise-loop",
        [
            {
                "id": "plan-loop",
                "type": "loop",
                "max_iterations": 3,
                "until": ['steps.approve.outputs.selected == "approve"'],
                "on_max_iterations": "prompt_user",
                "do": [
                    {
                        "id": "plan",
                        "type": "agent",
                        "prompt": (
                            "Plan: ${{ inputs.feature }}. Write PLAN.md.\n"
                            "Previous plan: {{ artifacts.plan.content }}\n"
                            "Feedback to address: {{ steps.approve.outputs.comment }}\n"
                        ),
                        "artifacts": [{"name": "plan", "path": "PLAN.md"}],
                    },
                    {
                        "id": "approve",
                        "type": "ask",
                        "interaction": {
                            "mode": "select",
                            "message": "Approve this plan?",
                            "artifact": "plan",
                            "comment": {"policy": "optional", "message": "What should change?"},
                            "options": [
                                {"value": "approve", "label": "Approve", "comment": "disabled"},
                                {"value": "revise", "label": "Request changes", "comment": "required"},
                                {"value": "deny", "label": "Deny", "fail": True},
                            ],
                        },
                    },
                ],
            }
        ],
    )


def review_triage_blueprint() -> dict[str, object]:
    """Return the issue's review-triage blueprint document."""
    return _blueprint(
        "review-triage",
        [
            {
                "id": "review-loop",
                "type": "loop",
                "max_iterations": 3,
                "until": ["steps.triage.outputs.selected_count == 0"],
                "do": [
                    {
                        "id": "review",
                        "type": "agent",
                        "prompt": (
                            "Review the changes. "
                            'Write review.json: {"findings":[{"id","severity","summary","detail"}]}.'
                        ),
                        "tools": {"allow": [{"capability": "read"}, {"capability": "write"}]},
                        "artifacts": [{"name": "review", "path": "review.json"}],
                    },
                    {
                        "id": "triage",
                        "type": "ask",
                        "interaction": {
                            "mode": "multiselect",
                            "message": "Which findings should the agent fix?",
                            "options_from": {
                                "artifact": "review",
                                "file": "review.json",
                                "items_path": "findings",
                                "value_field": "id",
                                "label_field": "summary",
                            },
                            "comment": {"policy": "optional"},
                        },
                    },
                    {
                        "id": "fix",
                        "type": "agent",
                        "prompt": (
                            "Fix only these findings: {{ steps.triage.outputs.selected }}\n"
                            "Details: {{ artifacts.review.content }}\n"
                            "Notes: {{ steps.triage.outputs.comment }}\n"
                        ),
                    },
                ],
            }
        ],
    )


def scripted_loop_blueprint() -> dict[str, object]:
    """Return the revise loop with command steps writing PLAN.md and recording the comment instead of agent steps."""
    document = revise_loop_blueprint()
    steps = document["steps"]
    assert isinstance(steps, list)
    loop = steps[0]
    assert isinstance(loop, dict)
    body = loop["do"]
    assert isinstance(body, list)
    body[0] = {
        "id": "plan",
        "type": "command",
        "command": (
            'echo "run" >> producer-runs.txt; '
            'echo "feedback: {{ steps.approve.outputs.comment }}" >> feedback.txt; '
            'echo "plan revised for: {{ steps.approve.outputs.comment }}" > PLAN.md'
        ),
        "artifacts": [{"name": "plan", "path": "PLAN.md"}],
    }
    return document
