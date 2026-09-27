"""Shared step-run engine and sandbox lifecycle for task/workflow execution."""

from worktree.core.runtime.exceptions import PromptUserInterruptedError
from worktree.core.runtime.failure import USER_CONTINUED_MARKER, effective_terminal_policy
from worktree.core.runtime.loop_runner import LoopBlockRunner
from worktree.core.runtime.models import (
    FailurePromptDecision,
    FailurePrompter,
    LoopPromptDecision,
    RunCheckpoint,
    RunContext,
    RunLogEvent,
    RunLogEventType,
    RunObserver,
    RunOutcome,
    RunPauseStore,
    StepLoopState,
    parse_checkpoint,
)
from worktree.core.runtime.run import run_steps
from worktree.core.step import (
    BlueprintMetadata,
    ExecutionIdentity,
    ExecutionMetadata,
    PreviousStepMetadata,
    StepMetadata,
)
from worktree.core.step.services.metadata import (
    build_execution_metadata,
    metadata_to_env,
    previous_step_metadata_from_result,
)

__all__ = [
    "USER_CONTINUED_MARKER",
    "BlueprintMetadata",
    "ExecutionIdentity",
    "ExecutionMetadata",
    "FailurePromptDecision",
    "FailurePrompter",
    "LoopBlockRunner",
    "LoopPromptDecision",
    "PreviousStepMetadata",
    "PromptUserInterruptedError",
    "RunCheckpoint",
    "RunContext",
    "RunLogEvent",
    "RunLogEventType",
    "RunObserver",
    "RunOutcome",
    "RunPauseStore",
    "StepLoopState",
    "StepMetadata",
    "build_execution_metadata",
    "effective_terminal_policy",
    "metadata_to_env",
    "parse_checkpoint",
    "previous_step_metadata_from_result",
    "run_steps",
]
