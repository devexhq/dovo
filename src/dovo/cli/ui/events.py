"""Pydantic DTO models representing runtime UI events."""

from __future__ import annotations

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.models import Remediation
from dovo.core.agents import AgentProgressKind
from dovo.core.db import SessionStatus


class ErrorPanelEvent(BaseModel):
    """UI event representing an error panel."""

    model_config = {"extra": "forbid", "strict": True}

    title: str
    message: str
    border_style: str = "red"


class BlueprintInvalidEvent(BaseModel):
    """UI event for a blueprint that failed validation before any step ran: every error, with the code and remediations `dovo blueprint validate` reports."""

    model_config = {"extra": "forbid", "strict": True}

    blueprint: str
    error_code: str
    errors: list[str]
    remediations: list[Remediation]


class WarningEvent(BaseModel):
    """UI event representing a warning notice."""

    model_config = {"extra": "forbid", "strict": True}

    message: str


class DiagnosticsEvent(BaseModel):
    """UI event listing the diagnostic-log records a run recorded, shown in terminal output after its warnings and outcome."""

    model_config = {"extra": "forbid", "strict": True}

    diagnostics: list[DiagnosticRecord]


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


class AgentSummaryView(BaseModel):
    """Presentation view of the JSON summary an agent step prints when it finishes."""

    model_config = {"extra": "forbid", "strict": True}

    status: str
    succeeded: bool
    summary: str | None = None
    unfixable_reason: str | None = None
    touched_files: list[str] = []


class StepOutputEvent(BaseModel):
    """UI event representing a stream output line emitted by a running step.

    ``agent_summary`` is set when the line is an agent step's summary; it never reaches the JSON wire format.
    """

    model_config = {"extra": "forbid", "strict": True}

    step_id: str
    line: str
    stream: str = "stdout"
    agent_summary: AgentSummaryView | None = Field(default=None, exclude=True)


class AgentProgressEvent(BaseModel):
    """UI event representing one agent action, note or tool result.

    A RESULT event holds an excerpt of the output in ``target``, its full ``line_count``,
    and the ``call_kind`` of the action that produced it.
    """

    model_config = {"extra": "forbid", "strict": True}

    step_id: str
    kind: AgentProgressKind
    target: str
    call_kind: AgentProgressKind | None = None
    line_count: int = 0
    is_error: bool = False


class WorktreeLifecycleEvent(BaseModel):
    """UI event representing worktree creation, activation, or cleanup."""

    model_config = {"extra": "forbid", "strict": True}

    action: str
    path: str
    active: bool | None = None
    kept: bool | None = None
    worktree_id: str | None = None
    branch: str | None = None
    remediations: list[Remediation] = []


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
