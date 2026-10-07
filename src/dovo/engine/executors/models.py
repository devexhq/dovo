from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import BaseResult
from dovo.core.agents.models import AgentResponseStatus
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.db.repositories.artifacts import ArtifactsRepository


class AssertionResult(BaseResult):
    """Aggregate result of evaluating a step's assert block."""

    passed: bool
    failed_conditions: list[str] = Field(default_factory=list)
    message: str


class ConditionEvaluationResult(BaseResult):
    """Result of evaluating a single until condition expression."""

    expression: str
    passed: bool
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


class ExecutionIdentity(BaseModel):
    """Optional run-level identity passed into RunSettings."""

    model_config = {"extra": "forbid", "strict": True}

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


class StepDispatchOutcome(BaseModel):
    """Raw outcome of one or more step primitive dispatches (before finalization)."""

    model_config = {"extra": "forbid", "strict": True}

    status: str  # "completed" | "failed"
    exit_code: int
    stdout: str
    stderr: str
    error_message: str | None = None
    attempts: int = 1


OutputCallback = Callable[[str, str], None]
AgentStepRunner = Callable[[StepDefinition, Path, OutputCallback | None], StepDispatchOutcome]


class StepExecutionContext(BaseModel):
    """Structured metadata for a step execution."""

    model_config = {"extra": "forbid", "strict": True, "arbitrary_types_allowed": True}

    step: StepDefinition
    worktree_path: Path
    context: dict[str, Any] | None = None
    on_output: OutputCallback | None = None
    step_index: int = 1
    initial_attempt: int = 1
    iteration_index: int = 1
    identity: ExecutionIdentity | None = None
    previous_step: PreviousStepMetadata | None = None
    steps: Sequence[PreviousStepMetadata] | None = None
    session_tmp_dir: Path | None = None
    session_log_dir: Path | None = None
    save_attempt_logs: bool = True
    loop_iteration: int | None = None
    session_id: str | None = None
    artifacts_dir: Path | None = None
    artifacts_db: ArtifactsRepository | None = None
    paths: WorkspacePaths | None = None
    agent_runner: AgentStepRunner | None = None
    sensitive_variables: tuple[str, ...] = ()


class InternalCommandContext(BaseModel):
    """Structured inputs available to an in-process internal step command handler."""

    model_config = {"extra": "forbid", "strict": True, "arbitrary_types_allowed": True}

    worktree_path: Path
    session_id: str
    env: dict[str, str] = Field(default_factory=dict)
    artifacts_dir: Path | None = None
    artifacts_db: ArtifactsRepository | None = None


class AgentStepSummary(BaseModel):
    """JSON summary an agent step writes to stdout."""

    model_config = {"extra": "forbid", "strict": True}

    status: AgentResponseStatus
    summary: str | None = None
    unfixable_reason: str | None = None
    touched_files: list[str] = Field(default_factory=list)


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

    @property
    def ok(self) -> bool:
        """Return True if step finished successfully or was ignored."""
        return self.status in ("completed", "ignored")
