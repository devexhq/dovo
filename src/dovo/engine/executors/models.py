from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Protocol

from pydantic import BaseModel, Field

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import BaseResult
from dovo.core.agents.models import AgentProgress, AgentProgressKind, AgentResponseStatus
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition, StepType
from dovo.core.interaction import InteractionRequest


class AssertionResult(BaseResult):
    """Aggregate result of evaluating a step's assert block."""

    passed: bool
    failed_conditions: list[str] = Field(default_factory=list)
    message: str


class ConditionEvaluationResult(BaseResult):
    """Result of evaluating one condition expression of a loop's until or a step's when.

    resolved is False when an operand that references a step found no value: the step was skipped or has not run,
    or its result lacks the field or output key. Such an expression never passes. A JSON null output is resolved.
    """

    expression: str
    passed: bool
    resolved: bool = True
    actual: Any = None
    expected: Any = None
    detail: str = ""


class StepMetadata(BaseModel):
    """Execution metadata for the current step."""

    model_config = {"extra": "forbid", "strict": True}

    id: str
    name: str = ""
    index: int = Field(ge=1)
    attempt: int = Field(default=1, ge=1)


class BlueprintMetadata(BaseModel):
    """Execution metadata for the parent blueprint (if any)."""

    model_config = {"extra": "forbid", "strict": True}

    name: str = ""
    key: str = ""


class PreviousStepMetadata(BaseModel):
    """Execution metadata for the immediately prior step (if any)."""

    model_config = {"extra": "forbid", "strict": True}

    id: str = ""
    name: str = ""
    index: str = ""
    status: str = ""
    exit_code: str = ""
    outputs: dict[str, str] = Field(default_factory=dict, exclude=True)


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Optional run-level identity passed into RunSettings."""

    blueprint_name: str = ""
    blueprint_key: str = ""


class IterationMetadata(BaseModel):
    """Execution metadata for the current loop iteration."""

    model_config = {"extra": "forbid", "strict": True}

    index: int = Field(default=1, ge=1)


class TempMetadata(BaseModel):
    """Execution metadata for session/step scratch directories and the step output file."""

    model_config = {"extra": "forbid", "strict": True}

    session_dir: str = ""
    step_dir: str = ""
    output_file: str = ""


class ExecutionMetadata(BaseModel):
    """Structured metadata exposed to step execution (env + interpolation)."""

    model_config = {"extra": "forbid", "strict": True}

    step: StepMetadata
    blueprint: BlueprintMetadata = Field(default_factory=BlueprintMetadata)
    previous_step: PreviousStepMetadata = Field(default_factory=PreviousStepMetadata)
    steps: list[PreviousStepMetadata] = Field(default_factory=list)
    iteration: IterationMetadata = Field(default_factory=IterationMetadata)
    tmp: TempMetadata = Field(default_factory=TempMetadata)
    session_id: str = ""
    # Unlike iteration.index (1 outside loops), None here means the step is not inside a loop.
    loop_iteration: int | None = None


DiagnosticContext = Mapping[str, str | int | float | bool | None]


class StepDispatchOutcome(BaseModel):
    """Raw outcome of one or more step primitive dispatches (before finalization)."""

    model_config = {"extra": "forbid", "strict": True}

    status: str  # "completed" | "failed"
    exit_code: int
    stdout: str
    stderr: str
    error_message: str | None = None
    attempts: int = 1
    rejected_patch_path: str | None = None
    diagnostics: list[DiagnosticRecord] = Field(
        default_factory=list
    )  # Records for the run's diagnostics; never persisted.


class StepPostcondition(Protocol):
    """A check applied to a completed dispatch outcome after the command succeeds."""

    def apply(self, outcome: StepDispatchOutcome, attempt: int) -> StepDispatchOutcome:
        """Return outcome unchanged, or a failed copy with an error_message."""
        ...


OutputCallback = Callable[[str, str], None]


class AgentProgressUpdate(BaseModel):
    """One masked, size-capped progress item delivered to observers.

    A RESULT update holds an excerpt of the call's output in ``target``, the output's full ``line_count``,
    and the ``call_kind`` of the action that produced it.
    """

    model_config = {"extra": "forbid", "strict": True}

    kind: AgentProgressKind
    target: str
    call_kind: AgentProgressKind | None = None
    line_count: int = 0
    is_error: bool = False


ProgressCallback = Callable[[AgentProgressUpdate], None]


class AgentProgressSink(Protocol):
    """Receives provider progress and tells the heartbeat how recently some arrived."""

    def report(self, progress: AgentProgress) -> None:
        """Record one progress item."""
        ...

    def seconds_since_last_report(self) -> float | None:
        """Return whole seconds since the last item, or None when none has arrived."""
        ...


AgentStepRunner = Callable[
    [StepDefinition, Path, OutputCallback | None, OutputCallback | None, AgentProgressSink, ExecutionMetadata],
    StepDispatchOutcome,
]


def require_agent_runner(step: StepDefinition, runner: AgentStepRunner | None) -> AgentStepRunner:
    """Return runner, raising ValueError naming the step when an agent step has none."""
    if runner is None:
        raise ValueError(f"Step '{step.id}' is an agent step and requires an agent runner.")
    return runner


@dataclass(frozen=True, slots=True)
class StepExecutionContext:
    """Structured metadata for a step execution."""

    step: StepDefinition
    worktree_path: Path
    # The caller's list for the run's diagnostics; the step extends it as each record is written.
    diagnostics: list[DiagnosticRecord]
    inputs: dict[str, str | int | bool] | None = None
    on_output: OutputCallback | None = None
    on_progress: ProgressCallback | None = None
    step_index: int = 1
    initial_attempt: int = 1
    identity: ExecutionIdentity | None = None
    previous_step: PreviousStepMetadata | None = None
    steps: Sequence[PreviousStepMetadata] | None = None
    session_tmp_dir: Path | None = None
    session_log_dir: Path | None = None
    save_attempt_logs: bool = True
    loop_iteration: int | None = None
    loop_id: str | None = None
    session_id: str | None = None
    artifact_store: ArtifactStore | None = None
    paths: WorkspacePaths | None = None
    agent_runner: AgentStepRunner | None = None
    sensitive_variables: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject an agent step that has no agent runner."""
        if self.step.type == StepType.AGENT:
            require_agent_runner(self.step, self.agent_runner)


class InternalCommandContext(BaseModel):
    """Structured inputs available to an in-process internal step command handler."""

    model_config = {"extra": "forbid", "strict": True, "arbitrary_types_allowed": True}

    worktree_path: Path
    env: dict[str, str] = Field(default_factory=dict)
    artifact_store: ArtifactStore | None = None
    step_id: str | None = None
    attempt: int | None = None
    loop_iteration: int | None = None


# Step outcome code per agent status; 200 is unused.
AGENT_OUTCOME_EXIT_CODES: Final[Mapping[AgentResponseStatus, int]] = MappingProxyType(
    {
        AgentResponseStatus.PROPOSED_PATCH: 0,
        AgentResponseStatus.NO_OP: 0,
        AgentResponseStatus.UNFIXABLE: 201,
        AgentResponseStatus.TIMEOUT: 202,
        AgentResponseStatus.PROVIDER_ERROR: 203,
        AgentResponseStatus.BLOCKED: 204,
    }
)


class AgentStepSummary(BaseModel):
    """JSON summary an agent step writes to stdout."""

    model_config = {"extra": "forbid", "strict": True}

    status: AgentResponseStatus
    summary: str | None = None
    unfixable_reason: str | None = None
    touched_files: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """Whether the status maps to a zero step exit code."""
        return AGENT_OUTCOME_EXIT_CODES[self.status] == 0


class StepResult(BaseResult):
    """Normalized result of a step execution."""

    step_id: str
    status: str  # "completed" | "failed" | "ignored"
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    attempts: int = 1
    error_message: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    rejected_patch_path: str | None = None

    @property
    def ok(self) -> bool:
        """Return True if step finished successfully or was ignored."""
        return self.status in ("completed", "ignored")


class AskResolution(BaseModel):
    """Outcome of preparing an ask step: a request to ask, or a result that settles the step without asking."""

    model_config = {"extra": "forbid", "strict": True}

    request: InteractionRequest | None = None
    result: StepResult | None = None
