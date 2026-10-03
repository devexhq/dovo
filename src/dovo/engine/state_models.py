"""Versioned execution-state tree persisted on a run row, and its load/save result types."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from dovo.common.models import BaseResult, FailurePolicy
from dovo.core.db import RunStatus
from dovo.engine.executors.models import StepResult
from dovo.engine.models import DefinitionsManifest


class NodeState(StrEnum):
    """Lifecycle state of an execution node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    IGNORED = "ignored"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


TERMINAL_NODE_STATES = frozenset({NodeState.COMPLETED, NodeState.IGNORED, NodeState.FAILED, NodeState.CANCELLED})


class StepAttemptRecord(BaseModel):
    """One attempt at running a step."""

    model_config = {"extra": "forbid", "strict": True}

    number: int = Field(ge=1)
    started_at: str
    completed_at: str | None = None
    result: StepResult | None = None  # None while attempt is running; populated on complete/fail


class ExecutionLeafNode(BaseModel):
    """A single step in the execution plan."""

    model_config = {"extra": "forbid", "strict": True}

    kind: Literal["step"] = "step"
    id: str
    name: str | None = None
    state: NodeState = NodeState.PENDING
    attempts: list[StepAttemptRecord] = Field(default_factory=list)


class ExecutionIterationRecord(BaseModel):
    """One iteration of a loop node."""

    model_config = {"extra": "forbid", "strict": True}

    number: int = Field(ge=1)
    state: NodeState = NodeState.PENDING
    steps: list[ExecutionLeafNode] = Field(default_factory=list)
    until_passed: bool | None = None  # None until evaluated; True if conditions passed; False if failed


class ExecutionLoopNode(BaseModel):
    """A loop block in the execution plan with its frozen configuration."""

    model_config = {"extra": "forbid", "strict": True}

    kind: Literal["loop"] = "loop"
    id: str
    max_iterations: int = Field(ge=1)
    until: list[str] = Field(default_factory=list)
    on_max_iterations: FailurePolicy = FailurePolicy.PROMPT_USER
    state: NodeState = NodeState.PENDING
    iterations: list[ExecutionIterationRecord] = Field(default_factory=list)
    granted_iterations: int = Field(default=0, ge=0)

    @property
    def iteration_ceiling(self) -> int:
        """Return max_iterations plus every granted iteration."""
        return self.max_iterations + self.granted_iterations


ExecutionPlanNode = Annotated[ExecutionLeafNode | ExecutionLoopNode, Field(discriminator="kind")]


class ExecutionStateTree(BaseModel):
    """Canonical, revisioned execution state of one run."""

    model_config = {"extra": "forbid", "strict": True}

    schema_version: int = 1
    revision: int = 0
    manifest: DefinitionsManifest
    nodes: list[ExecutionPlanNode] = Field(default_factory=list)


class RunLifecycle(BaseModel):
    """Row-derived lifecycle outcome embedded in run.json."""

    model_config = {"extra": "forbid", "strict": True}

    status: RunStatus
    error_message: str | None = None
    started_at: str
    completed_at: str | None = None
    worktree_id: str | None = None
    worktree_kept: bool = False


class RunJsonPayload(BaseModel):
    """Database-derived run.json projection: frozen manifest, execution tree, lifecycle, and flattened results."""

    model_config = {"extra": "forbid", "strict": True}

    schema_version: int = 1
    revision: int
    manifest: DefinitionsManifest
    nodes: list[ExecutionPlanNode] = Field(default_factory=list)
    lifecycle: RunLifecycle
    results: list[StepResult] = Field(default_factory=list)


class RunStateWriteStatus(StrEnum):
    """Classified outcomes for RunStateStore.initialize and RunStateStore.save."""

    OK = "ok"
    NOT_FOUND = "not_found"
    REVISION_CONFLICT = "revision_conflict"


class RunStateWriteResult(BaseResult):
    """Result of committing an execution-state revision to a run row."""

    status: RunStateWriteStatus
    state: ExecutionStateTree | None = None

    @property
    def ok(self) -> bool:
        """Return True when the revision was committed."""
        return self.status == RunStateWriteStatus.OK


class RunStateLoadStatus(StrEnum):
    """Classified outcomes for RunStateStore.load."""

    OK = "ok"
    NOT_FOUND = "not_found"
    MISSING_STATE = "missing_state"
    CORRUPT_STATE = "corrupt_state"
    INCONSISTENT_PROJECTION = "inconsistent_projection"
    MISSING_SNAPSHOT = "missing_snapshot"


class RunStateLoadResult(BaseResult):
    """Result of loading and validating a run's execution state."""

    status: RunStateLoadStatus
    state: ExecutionStateTree | None = None

    @property
    def ok(self) -> bool:
        """Return True when a validated state is available."""
        return self.status == RunStateLoadStatus.OK
