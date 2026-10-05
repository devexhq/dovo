"""Pydantic DTO models representing runtime UI events."""

from __future__ import annotations

from pydantic import BaseModel

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


class PromptOption(BaseModel):
    """Option presented to the user during an interactive prompt."""

    model_config = {"extra": "forbid", "strict": True}

    key: str
    label: str
    decision: str


class PromptEvent(BaseModel):
    """UI event representing an interactive prompt for step failure or loop max iterations."""

    model_config = {"extra": "forbid", "strict": True}

    prompt_type: str  # "step_failure" | "loop_max_iterations"
    prompt_id: str  # step ID or loop ID
    title: str
    diagnostic: str | None = None
    options: list[PromptOption]
    default: str = "abort"
