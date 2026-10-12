"""Pydantic DTO models representing runtime UI events."""

from __future__ import annotations

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer

from dovo.core.db import SessionStatus


class ErrorPanelEvent(BaseModel):
    """UI event representing an error panel."""

    model_config = {"extra": "forbid", "strict": True}

    title: str
    message: str
    border_style: str = "red"


class WarningEvent(BaseModel):
    """UI event representing a warning notice."""

    model_config = {"extra": "forbid", "strict": True}

    message: str


class LockWaitEvent(BaseModel):
    """UI event representing waiting on an advisory workspace lock."""

    model_config = {"extra": "forbid", "strict": True}

    lock_path: str
    holder_pid: str | None = None
    timeout_seconds: float


class MessageEvent(BaseModel):
    """UI event representing a general status notice or text line."""

    model_config = {"extra": "forbid", "strict": True}

    message: str
    style: str | None = None


class RunSuccessEvent(BaseModel):
    """UI event representing blueprint run completion."""

    model_config = {"extra": "forbid", "strict": True}

    session_id: str
    blueprint_name: str
    status: SessionStatus


class StepStartEvent(BaseModel):
    """UI event representing the beginning of a step."""

    model_config = {"extra": "forbid", "strict": True}

    idx: int
    total: int
    step_id: str
    name: str | None = None
    command: str | None = None


class StepDoneEvent(BaseModel):
    """UI event representing the completion of a step."""

    model_config = {"extra": "forbid", "strict": True}

    idx: int
    total: int
    step_id: str
    ok: bool
    exit_code: int
    duration_seconds: float | None = None
    error_message: str | None = None


class StepOutputEvent(BaseModel):
    """UI event representing a stream output line emitted by a running step."""

    model_config = {"extra": "forbid", "strict": True}

    step_id: str
    line: str
    stream: str = "stdout"


class WorktreeLifecycleEvent(BaseModel):
    """UI event representing worktree creation, activation, or cleanup."""

    model_config = {"extra": "forbid", "strict": True}

    action: str
    path: str
    active: bool | None = None
    kept: bool | None = None
    worktree_id: str | None = None
    branch: str | None = None


class LoopConditionView(BaseModel):
    """Presentation view of a single evaluated until-condition."""

    model_config = {"extra": "forbid", "strict": True}

    expression: str
    passed: bool
    detail: str


class LoopLifecycleEvent(BaseModel):
    """UI event representing loop block progress and evaluation."""

    model_config = {"extra": "forbid", "strict": True}

    loop_id: str
    action: str
    iteration: int | None = None
    max_iterations: int | None = None
    status: str | None = None
    message: str | None = None
    conditions: list[LoopConditionView] = []
    next_iteration: int | None = None


class WelcomeBannerEvent(BaseModel):
    """UI event representing the welcome brand panel."""

    model_config = {"extra": "forbid", "strict": True}

    version: str


class PromptEventArtifact(BaseModel):
    """Artifact shown above an interaction prompt; `digest` is the 8-character short form."""

    model_config = {"extra": "forbid", "strict": True}

    name: str
    revision: int
    digest: str
    reference: str
    preview: list[str] = []


class PromptEventComment(BaseModel):
    """Comment policy and prompt text of an interaction prompt."""

    model_config = {"extra": "forbid", "strict": True}

    policy: str
    message: str


class PromptOption(BaseModel):
    """Option presented to the user during an interactive prompt."""

    model_config = {"extra": "forbid", "strict": True}

    key: str
    label: str
    decision: str
    comment: str | None = None
    fail: bool | None = None
    display: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_interaction_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Drop `comment`, `fail` and `display` from the wire payload when unset so failure prompts keep their original shape."""
        data = handler(self)
        interaction_only = ("comment", "fail", "display")
        return {key: value for key, value in data.items() if key not in interaction_only or value is not None}


class PromptEvent(BaseModel):
    """UI event representing an interactive prompt for step failure, loop max iterations or an ask step."""

    model_config = {"extra": "forbid", "strict": True}

    prompt_type: str  # "step_failure" | "loop_max_iterations" | "interaction"
    step_id: str  # step ID; a loop prompt carries the loop's step ID
    title: str
    diagnostic: str | None = None
    options: list[PromptOption]
    default: str | None = "abort"
    mode: str | None = None
    message: str | None = None
    artifact: PromptEventArtifact | None = None
    comment: PromptEventComment | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_interaction_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Drop the interaction-only fields from the wire payload when unset so failure prompts keep their original shape."""
        data = handler(self)
        interaction_only = ("mode", "message", "artifact", "comment")
        return {key: value for key, value in data.items() if key not in interaction_only or value is not None}
