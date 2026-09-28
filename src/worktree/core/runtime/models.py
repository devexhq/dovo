"""Shared run-context models for step execution engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from worktree.common.filesystem import WorkspacePaths
from worktree.core.config.models import WorktreeConfig
from worktree.core.db import RunStatus
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.sandbox import SandboxSession
from worktree.core.step import (
    ConditionEvaluationResult,
    ExecutionIdentity,
    LoopStepBlock,
    StepDefinition,
    StepResult,
)


class FailurePromptDecision(StrEnum):
    """User (or adapter) decision after a terminal ``prompt_user`` step failure."""

    RETRY = "retry"
    CONTINUE = "continue"
    ABORT = "abort"


class LoopPromptDecision(StrEnum):
    """User (or adapter) decision when loop reaches max_iterations."""

    GRANT = "grant"
    CONTINUE = "continue"
    ABORT = "abort"


@runtime_checkable
class FailurePrompter(Protocol):
    """Injectable decision entrypoint for interactive step-failure and loop-handling."""

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        """Return the caller's decision. Must not block for non-interactive callers."""
        ...

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,  # @TODO: Make this configurable
    ) -> LoopPromptDecision:
        """Return the caller's decision when a loop reaches max_iterations."""
        ...


class RunCheckpoint(BaseModel):
    """JSON-serializable pause payload sufficient to resume a run."""

    model_config = {"extra": "forbid", "strict": True}

    version: int = 1
    next_step_index: int
    step_results: list[StepResult] = Field(default_factory=list)
    sandbox_path: str | None = None
    sandbox_id: str | None = None
    sandbox_name: str | None = None
    sandbox_branch: str | None = None
    sandbox_base_commit: str | None = None
    use_sandbox: bool = True
    keep: bool = False
    agent: str | None = None
    inputs: dict[str, str | int | bool] = Field(default_factory=dict)
    identity: ExecutionIdentity | None = None
    pending_step_id: str
    diagnostic: str
    pending_result: StepResult | None = None


class RunPauseStore(Protocol):
    """Domain adapter that persists and clears durable pause checkpoints."""

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        """Write checkpoint JSON and set the tracked run to paused."""
        ...

    def clear_pause(self) -> None:
        """Mark the tracked run running again after an in-process prompt returns."""
        ...


def parse_checkpoint(raw: str | None) -> RunCheckpoint | None:
    """Load a checkpoint from JSON, or return None when missing or corrupt."""
    if raw is None or not raw.strip():
        return None
    try:
        return RunCheckpoint.model_validate_json(raw)
    except (ValueError, TypeError):
        return None


@dataclass
class StepLoopState:
    """Mutable per-run bookkeeping threaded through the step loop."""

    target_dir: Path
    session: SandboxSession | None
    step_results: list[StepResult] = field(default_factory=list)
    session_tmp_dir: Path | None = None
    session_log_dir: Path | None = None
    save_attempt_logs: bool = True
    warnings: list[str] = field(default_factory=list)
    artifacts_dir: Path | None = None
    artifacts_db: ArtifactsRepository | None = None


@dataclass(frozen=True)
class RunContext:
    """Immutable inputs for a multi-step run."""

    steps: list[StepDefinition | LoopStepBlock]
    cwd: Path
    use_sandbox: bool = True
    keep: bool = False
    agent: str | None = None
    observer: RunObserver | None = None
    inputs: dict[str, str | int | bool] | None = None
    identity: ExecutionIdentity | None = None
    session_id: str | None = None
    no_tty: bool = False
    failure_prompter: FailurePrompter | None = None
    pause_store: RunPauseStore | None = None
    resume_from: RunCheckpoint | None = None
    auto_apply: bool = False
    config: WorktreeConfig | None = None
    paths: WorkspacePaths = field(kw_only=True)


@runtime_checkable
class RunObserver(Protocol):
    """Optional progress hooks for sandbox, step, and loop lifecycle events."""

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        """Called after the execution directory is chosen."""
        ...

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        """Called immediately before a step begins."""
        ...

    def on_step_output(
        self,
        idx: int,
        total: int,
        step: StepDefinition,
        line: str,
        stream: str = "stdout",
    ) -> None:
        """Called when a running step emits a line of output."""
        ...

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
        """Called immediately after a step finishes."""
        ...

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        """Called when a loop block begins execution."""
        ...

    def on_loop_turn_start(self, loop_id: str, turn: int, max_iterations: int) -> None:
        """Called at the start of a loop turn."""
        ...

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_turn: int | None = None,
    ) -> None:
        """Called after loop until conditions are evaluated for a turn."""
        ...

    def on_loop_done(self, loop_id: str, status: str, turns: int) -> None:
        """Called when a loop block finishes."""
        ...

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        """Called after sandbox cleanup/keep decision is applied."""
        ...


class RunLogEventType(StrEnum):
    """Discriminates which optional fields a RunLogEvent populates."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    STEP_START = "step_start"
    STEP_DONE = "step_done"
    LOOP_START = "loop_start"
    LOOP_TURN_START = "loop_turn_start"
    LOOP_CONDITIONS_EVALUATED = "loop_conditions_evaluated"
    LOOP_DONE = "loop_done"


class RunLogEvent(BaseModel):
    """One structured, ISO-timestamped run.log timeline record."""

    model_config = {"extra": "forbid", "strict": True}

    ts: str = ""
    event: RunLogEventType
    session_id: str | None = None
    blueprint_key: str | None = None
    step_index: int | None = None
    step_id: str | None = None
    attempt: int | None = None
    status: str | None = None
    exit_code: int | None = None
    loop_id: str | None = None
    turn: int | None = None
    max_iterations: int | None = None
    all_passed: bool | None = None
    next_turn: int | None = None
    conditions: list[dict[str, object]] | None = None

    def details(self) -> dict[str, object]:
        """Return the populated scalar fields beyond ts and event, in declaration order; conditions are omitted."""
        return self.model_dump(exclude={"ts", "event", "conditions"}, exclude_none=True)


class RunOutcome(BaseModel):
    """Structured result of ``run_steps``."""

    model_config = {"extra": "forbid", "strict": True}

    status: RunStatus
    step_results: list[StepResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sandbox_kept: bool = False
    sandbox_path: Path
    session_id: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the run completed successfully."""
        return self.status == RunStatus.COMPLETED and not self.errors
