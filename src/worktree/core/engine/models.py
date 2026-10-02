"""Result types for the blueprint execution engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from worktree.common.filesystem import WorkspacePaths
from worktree.common.models import BaseResult
from worktree.core.agents.models import ResolvedAgentSettings
from worktree.core.db import RunRecord, RunStatus
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.sandbox import SandboxSession
from worktree.core.step import (
    ConditionEvaluationResult,
    ExecutionIdentity,
    LoopStepBlock,
    StepDefinition,
    StepResult,
)


class EngineResumeStatus(StrEnum):
    """Classified outcomes for ``EngineLoader.load_for_resume`` / ``Engine.resume``."""

    OK = "ok"
    NOT_FOUND = "not_found"
    WRONG_STATUS = "wrong_status"
    MISSING_SANDBOX = "missing_sandbox"
    CORRUPT_STATE = "corrupt_state"
    MISSING_SNAPSHOT = "missing_snapshot"
    FAILED = "failed"


@dataclass(frozen=True)
class RunRequest:
    """Caller options for ``Engine.run``."""

    inputs: dict[str, str | int | bool] | None = None
    cli_args: list[str] | None = None
    use_sandbox: bool | None = None
    keep: bool = False
    agent: str | None = None
    session_id: str | None = None
    observer: RunObserver | None = None
    failure_prompter: FailurePrompter | None = None
    no_tty: bool = False
    auto_apply: bool = False


@dataclass(frozen=True)
class RunStartConfig:
    """Resolved run options persisted on the run row at start."""

    blueprint_tier: str | None
    commit_sha: str | None
    use_sandbox: bool
    keep: bool
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


class ReconciliationResult(BaseResult):
    """Result of reconciling stale running sessions."""

    reconciled: list[RunRecord] = Field(default_factory=list)
    warning: str | None = None


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


class AgentSettingsResolution(BaseResult):
    """Result of resolving the effective agent settings for one run drive."""

    settings: ResolvedAgentSettings | None = None

    @property
    def ok(self) -> bool:
        """Return True when settings resolved."""
        return self.settings is not None


@dataclass(frozen=True)
class RunSettings:
    """Settings and collaborators resolved from the run row, consumed by Workspace and StepCoordinator.

    ``agent`` comes from effective config, not the row: the row only overrides its provider.
    """

    cwd: Path
    use_sandbox: bool = True
    keep: bool = False
    agent: ResolvedAgentSettings | None = None
    observer: RunObserver | None = None
    inputs: dict[str, str | int | bool] | None = None
    identity: ExecutionIdentity | None = None
    session_id: str | None = None
    no_tty: bool = False
    failure_prompter: FailurePrompter | None = None
    auto_apply: bool = False
    sandbox_id: str | None = None
    paths: WorkspacePaths = field(kw_only=True)


@dataclass(frozen=True)
class RunContext:
    """Infrastructure resources for one run's execution; durable progress lives only in ExecutionStateTree."""

    session_id: str
    paths: WorkspacePaths
    target_dir: Path
    session_tmp_dir: Path | None
    session_log_dir: Path | None
    artifacts_dir: Path | None
    artifacts_db: ArtifactsRepository | None = None
    sandbox: SandboxSession | None = None
    no_tty: bool = False
    save_attempt_logs: bool = True


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

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        """Called immediately after a step finishes."""
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

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        """Called after sandbox cleanup/keep decision is applied."""
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

    status: RunStatus
    step_results: list[StepResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sandbox_kept: bool = False
    sandbox_path: Path
    session_id: str | None = None
    sandbox_id: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the run completed successfully."""
        return self.status == RunStatus.COMPLETED and not self.errors
