"""Result types for the blueprint execution engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from dovo.common.clock import Clock
from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.filesystem import WorkspacePaths
from dovo.common.models import BaseResult, Remediation
from dovo.common.session_id import SessionIdFactory
from dovo.core.agents.models import AgentEnvOverrides, ResolvedAgentSettings
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition
from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.core.interaction import InteractionAnswer, InteractionRequest
from dovo.core.worktree import WorktreeSession
from dovo.engine.executors.models import (
    AgentProgressUpdate,
    ConditionEvaluationResult,
    ExecutionIdentity,
    PreviousStepMetadata,
    StepResult,
)


@dataclass(frozen=True)
class RunRequest:
    """Caller options for ``Engine.run``."""

    inputs: dict[str, str | int | bool] | None = None
    cli_args: list[str] | None = None
    use_worktree: bool | None = None
    keep: bool = False
    discard: bool = False
    agent: str | None = None
    session_id: str | None = None
    observer: RunObserver | None = None
    failure_prompter: FailurePrompter | None = None
    interaction_prompter: InteractionPrompter | None = None
    no_tty: bool = False
    auto_apply: bool = False
    env_overrides: AgentEnvOverrides | None = None


@dataclass(frozen=True)
class LifecycleUpdate:
    """Session row lifecycle fields committed with an execution-state save.

    A None ``run_status``, ``worktree_id`` or ``worktree_kept`` leaves that field unchanged.
    ``error_message`` is written only with a ``run_status``, which replaces the stored message,
    clearing it when ``error_message`` is None.
    """

    run_status: SessionStatus | None = None
    error_message: str | None = None
    worktree_id: str | None = None
    worktree_kept: bool | None = None


@dataclass(frozen=True)
class RunStartConfig:
    """Resolved run options persisted on the session row at start."""

    blueprint_tier: str | None
    commit_sha: str | None
    use_worktree: bool
    keep: bool
    discard: bool
    agent: str | None
    inputs: dict[str, str | int | bool]
    auto_apply: bool


class DefinitionRef(BaseModel):
    """One snapshotted catalog item's resolved reference, content hash, and resolution time."""

    model_config = {"extra": "forbid", "strict": True}

    ref: str
    sha: str
    resolved_at: str


class DefinitionsManifest(BaseModel):
    """Manifest of the blueprint and uses:-referenced steps snapshotted for a run's session."""

    model_config = {"extra": "forbid", "strict": True}

    blueprint: DefinitionRef
    steps: list[DefinitionRef] = Field(default_factory=list)


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


class SkipCause(StrEnum):
    """Why a `when` guard skipped a step: the first failing expression was false, or an operand did not resolve."""

    WHEN_FALSE = "when_false"
    WHEN_UNRESOLVED = "when_unresolved"


@dataclass(frozen=True, slots=True)
class StepSkipNotice:
    """A step or loop a `when` guard skipped, with the first failing expression and why it failed."""

    step_id: str
    name: str | None
    cause: SkipCause
    expression: str


class StepAction(StrEnum):
    """Orchestration action chosen after a terminal step failure; distinct from user prompt decisions and persisted node transitions."""

    RETRY = "retry"
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


@runtime_checkable
class InteractionPrompter(Protocol):
    """Injectable entrypoint that collects a human answer for an `ask` step."""

    def ask(self, request: InteractionRequest) -> InteractionAnswer | None:
        """Return the answer, or None when unanswered, interrupted or non-interactive."""
        ...


class AgentSettingsResolution(BaseResult):
    """Result of resolving the effective agent settings for one run drive."""

    settings: ResolvedAgentSettings | None = None

    @property
    def ok(self) -> bool:
        """Return True when settings resolved."""
        return self.settings is not None


@dataclass(frozen=True, slots=True)
class StepInvocation:
    """One step attempt's position and history."""

    step: StepDefinition
    idx: int
    total: int
    attempt: int = 1
    loop_id: str | None = None
    loop_iteration: int | None = None
    previous_step: PreviousStepMetadata = field(default_factory=PreviousStepMetadata)
    steps: tuple[PreviousStepMetadata, ...] = ()


@dataclass(frozen=True)
class RunSettings:
    """Settings and collaborators resolved from the session row, consumed by Workspace and RunCoordinator."""

    cwd: Path
    use_worktree: bool = True
    keep: bool = False
    discard: bool = False
    agent: ResolvedAgentSettings | None = None
    observer: RunObserver | None = None
    inputs: dict[str, str | int | bool] | None = None
    identity: ExecutionIdentity | None = None
    session_id: str | None = None
    no_tty: bool = False
    failure_prompter: FailurePrompter | None = None
    auto_apply: bool = False
    worktree_id: str | None = None
    sensitive_variables: tuple[str, ...] = ()
    paths: WorkspacePaths = field(kw_only=True)
    git: GitRunner = field(kw_only=True)
    clock: Clock = field(kw_only=True)
    new_id: SessionIdFactory = field(kw_only=True)


@dataclass(frozen=True)
class RunContext:
    """Infrastructure resources for one run's execution; durable progress lives only in ExecutionStateTree."""

    session_id: str
    paths: WorkspacePaths
    target_dir: Path
    session_tmp_dir: Path | None
    session_log_dir: Path | None
    artifact_store: ArtifactStore | None
    worktree: WorktreeSession | None = None
    no_tty: bool = False
    save_attempt_logs: bool = True
    sensitive_variables: tuple[str, ...] = ()
    interaction_prompter: InteractionPrompter | None = None
    git: GitRunner = field(kw_only=True)
    clock: Clock = field(kw_only=True)
    new_id: SessionIdFactory = field(kw_only=True)


@runtime_checkable
class RunObserver(Protocol):
    """Optional progress hooks for validation, worktree, step, and loop lifecycle events."""

    def on_validation_warnings(self, warnings: Sequence[str]) -> None:
        """Called once per run or resume, before the first step, with the blueprint's validation warnings in `dovo blueprint validate` order."""
        ...

    def on_worktree_ready(self, path: Path, active: bool) -> None:
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

    def on_step_progress(self, idx: int, total: int, step: StepDefinition, update: AgentProgressUpdate) -> None:
        """Called when a running agent step reports an action, a note or a tool result."""
        ...

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        """Called immediately after a step finishes."""
        ...

    def on_step_skipped(self, idx: int, total: int, step: StepSkipNotice) -> None:
        """Called when a `when` guard skips a step or loop, instead of on_step_start and on_step_done."""
        ...

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        """Called when a loop block begins execution."""
        ...

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        """Called at the start of a loop iteration."""
        ...

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        """Called after loop until conditions are evaluated for an iteration."""
        ...

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        """Called when a loop block finishes."""
        ...

    def on_worktree_cleanup(
        self,
        kept: bool,
        path: Path,
        worktree_id: str | None = None,
        branch: str | None = None,
        remediations: Sequence[Remediation] = (),
    ) -> None:
        """Called after worktree cleanup/keep decision is applied; worktree_id, branch and remediations are set when a run's worktree is kept for inspection."""
        ...

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        """Called once per drive_run invocation, after definitions load and before the first step."""
        ...

    def on_run_completed(self, outcome: RunOutcome) -> None:
        """Called once per drive_run invocation, after close-out, with the returned outcome."""
        ...


class RunOutcome(BaseModel):
    """Structured result of ``drive_run``."""

    model_config = {"extra": "forbid", "strict": True}

    status: SessionStatus
    step_results: list[StepResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)
    worktree_kept: bool = False
    worktree_path: Path
    session_id: str | None = None
    worktree_id: str | None = None
    paused_prompt_id: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the run completed successfully."""
        return self.status == SessionStatus.COMPLETED and not self.errors


class BlueprintRunResult(BaseResult):
    """Result of executing a blueprint.

    invalid_blueprint names the blueprint when validation stopped the run or resume before any step ran; errors then
    hold every validation error, and error_code and remediations are those `dovo blueprint validate` reports.
    Validation warnings reach the observer before any step and are not repeated in warnings.
    """

    session_record: SessionRecord | None = None
    paused_prompt_id: str | None = None
    invalid_blueprint: str | None = None

    @property
    def ok(self) -> bool:
        """Return True if run completed without fatal errors."""
        return self.session_record is not None and len(self.errors) == 0
