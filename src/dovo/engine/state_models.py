"""Versioned execution-state tree persisted on a session row, and its load/save result types."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from dovo.common.models import FailurePolicy
from dovo.common.timing import TimeSpan
from dovo.core.db import SessionStatus
from dovo.core.interaction import InteractionRequest
from dovo.engine.executors.models import StepResult
from dovo.engine.models import DefinitionsManifest, SkipCause


class NodeState(StrEnum):
    """Lifecycle state of an execution node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    IGNORED = "ignored"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


TERMINAL_NODE_STATES = frozenset(
    {NodeState.COMPLETED, NodeState.IGNORED, NodeState.FAILED, NodeState.CANCELLED, NodeState.SKIPPED}
)


class StepSkip(BaseModel):
    """The `when` expression that skipped a step or loop, and why it failed."""

    model_config = {"extra": "forbid", "strict": True}

    cause: SkipCause
    expression: str


class StepAttemptRecord(BaseModel):
    """One attempt at running a step."""

    model_config = {"extra": "forbid", "strict": True}

    number: int = Field(ge=1)
    span: TimeSpan
    result: StepResult | None = None  # None while attempt is running; populated on complete/fail
    ask: InteractionRequest | None = None  # The prompt an ask step's attempt was answered for; None otherwise


class ExecutionLeafNode(BaseModel):
    """A single step in the execution plan."""

    model_config = {"extra": "forbid", "strict": True}

    kind: Literal["step"] = "step"
    id: str
    name: str | None = None
    state: NodeState = NodeState.PENDING
    attempts: list[StepAttemptRecord] = Field(default_factory=list)
    pending_ask: InteractionRequest | None = None
    skip: StepSkip | None = None  # Set when a `when` guard skipped the step


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
    skip: StepSkip | None = None  # Set when a `when` guard skipped the loop

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


class SessionLifecycle(BaseModel):
    """Row-derived lifecycle outcome embedded in session.json."""

    model_config = {"extra": "forbid", "strict": True}

    status: SessionStatus
    error_message: str | None = None
    started_at: str
    completed_at: str | None = None
    worktree_id: str | None = None
    worktree_kept: bool = False


class SessionJsonPayload(BaseModel):
    """Database-derived session.json projection: frozen manifest, execution tree, lifecycle, and flattened results."""

    model_config = {"extra": "forbid", "strict": True}

    schema_version: int = 1
    revision: int
    manifest: DefinitionsManifest
    nodes: list[ExecutionPlanNode] = Field(default_factory=list)
    lifecycle: SessionLifecycle
    results: list[StepResult] = Field(default_factory=list)
