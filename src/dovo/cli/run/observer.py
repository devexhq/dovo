"""CLI RunObserver implementations routing execution lifecycle events to UiDispatcher."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import (
    AgentProgressEvent,
    AgentSummaryView,
    LoopConditionView,
    LoopLifecycleEvent,
    StepDoneEvent,
    StepOutputEvent,
    StepStartEvent,
    WorktreeLifecycleEvent,
)
from dovo.common.models import DisplayFormatOptions, OutputFormatOptions
from dovo.common.utils import display_path
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition, StepType
from dovo.engine import RunObserver, RunOutcome
from dovo.engine.executors.models import AgentProgressUpdate, AgentStepSummary, ConditionEvaluationResult, StepResult

if TYPE_CHECKING:
    from types import TracebackType


def _agent_summary_view(step: StepDefinition, line: str) -> AgentSummaryView | None:
    """Return the summary view when an agent step's stdout line is its JSON summary, else None."""
    if step.type is not StepType.AGENT or not line.startswith("{"):
        return None

    try:
        summary = AgentStepSummary.model_validate_json(line)
    except ValidationError:
        return None

    return AgentSummaryView(
        status=summary.status.value,
        succeeded=summary.succeeded,
        summary=summary.summary,
        unfixable_reason=summary.unfixable_reason,
        touched_files=summary.touched_files,
    )


class DispatcherRunObserver(RunObserver):
    """Observer adapter converting runtime lifecycle callbacks into UI events for UiDispatcher."""

    def __init__(self, dispatcher: UiDispatcher, *, live: bool = False) -> None:
        """Initialize observer.

        Args:
            dispatcher: UiDispatcher instance to route events through.
            live: Whether to coordinate an active live display session.
        """
        self._dispatcher = dispatcher
        self._live = live

    def __enter__(self) -> DispatcherRunObserver:
        """Enter observer context and optionally start live display."""
        if self._live:
            self._dispatcher.start_live()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit observer context and stop live display."""
        if self._live:
            self._dispatcher.stop_live()

    def on_worktree_ready(self, path: Path, active: bool) -> None:
        """Dispatch worktree readiness event."""
        self._dispatcher.dispatch(WorktreeLifecycleEvent(action="ready", path=str(path), active=active))

    def on_step_start(self, idx: int, total: int, step: StepDefinition) -> None:
        """Dispatch step start progress event."""
        self._dispatcher.dispatch(
            StepStartEvent(
                idx=idx,
                total=total,
                step_id=step.id,
                name=step.name,
                command=step.run,
            )
        )

    def on_step_output(
        self,
        idx: int,
        total: int,
        step: StepDefinition,
        line: str,
        stream: str = "stdout",
    ) -> None:
        """Dispatch live step output event."""
        text = line.rstrip("\r\n")
        self._dispatcher.dispatch(
            StepOutputEvent(
                step_id=step.id,
                line=text,
                stream=stream,
                agent_summary=_agent_summary_view(step, text) if stream == "stdout" else None,
            )
        )

    def on_step_progress(self, idx: int, total: int, step: StepDefinition, update: AgentProgressUpdate) -> None:
        """Dispatch an agent action, note or tool result event."""
        self._dispatcher.dispatch(
            AgentProgressEvent(
                step_id=step.id,
                kind=update.kind,
                target=update.target,
                call_kind=update.call_kind,
                line_count=update.line_count,
                is_error=update.is_error,
            )
        )

    def on_step_done(self, idx: int, total: int, step: StepDefinition, result: StepResult) -> None:
        """Dispatch step completion or failure event."""
        self._dispatcher.dispatch(
            StepDoneEvent(
                idx=idx,
                total=total,
                step_id=result.step_id,
                ok=result.ok,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                error_message=result.error_message,
            )
        )

    def on_loop_start(self, loop_id: str, max_iterations: int) -> None:
        """Dispatch loop start event."""
        self._dispatcher.dispatch(LoopLifecycleEvent(loop_id=loop_id, action="start", max_iterations=max_iterations))

    def on_loop_iteration_start(self, loop_id: str, iteration: int, max_iterations: int) -> None:
        """Dispatch loop iteration start event."""
        self._dispatcher.dispatch(
            LoopLifecycleEvent(
                loop_id=loop_id,
                action="iteration_start",
                iteration=iteration,
                max_iterations=max_iterations,
            )
        )

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_iteration: int | None = None,
    ) -> None:
        """Dispatch loop until conditions evaluated event."""
        lines = [f"\\[{loop_id}] Evaluated 'until' conditions:"]
        for r in results:
            lines.append(f"  - {r.expression}: {r.detail}")
        if not all_passed and next_iteration is not None:
            lines.append(f"\\[{loop_id}] Conditions not met. Continuing to iteration {next_iteration}...")
        self._dispatcher.dispatch(
            LoopLifecycleEvent(
                loop_id=loop_id,
                action="conditions_evaluated",
                message="\n".join(lines),
                conditions=[
                    LoopConditionView(expression=r.expression, passed=r.passed, detail=r.detail) for r in results
                ],
                next_iteration=next_iteration,
            )
        )

    def on_loop_done(self, loop_id: str, status: str, total_iterations: int) -> None:
        """Dispatch loop completion event."""
        self._dispatcher.dispatch(
            LoopLifecycleEvent(
                loop_id=loop_id,
                action="done",
                iteration=total_iterations,
                status=status,
            )
        )

    def on_worktree_cleanup(
        self, kept: bool, path: Path, worktree_id: str | None = None, branch: str | None = None
    ) -> None:
        """Dispatch worktree cleanup event."""
        self._dispatcher.dispatch(
            WorktreeLifecycleEvent(
                action="cleanup",
                path=display_path(path, Path.cwd()),
                kept=kept,
                worktree_id=worktree_id,
                branch=branch,
            )
        )

    def on_run_started(self, steps: Sequence[StepDefinition | LoopStepBlock]) -> None:
        """Ignore run start; the CLI renders step and loop events as they happen."""

    def on_run_completed(self, outcome: RunOutcome) -> None:
        """Ignore run completion; the run command renders the returned outcome."""


def resolve_cli_observer(
    dispatcher: UiDispatcher,
    *,
    no_tty: bool = False,
    output_format: OutputFormatOptions,
    display_format: DisplayFormatOptions,
) -> DispatcherRunObserver:
    """Return DispatcherRunObserver configured for the execution session.

    Args:
        dispatcher: The active UiDispatcher instance.
        no_tty: Whether non-interactive execution is requested.
        output_format: Output format ('terminal' or 'json').
        display_format: Display formata ('ansi' or 'live')

    Returns:
        A DispatcherRunObserver instance with live mode enabled if supported.
    """
    is_terminal_tty = output_format == OutputFormatOptions.TERMINAL and not no_tty and dispatcher.is_interactive
    if is_terminal_tty and display_format == DisplayFormatOptions.LIVE:
        return DispatcherRunObserver(dispatcher, live=True)
    return DispatcherRunObserver(dispatcher, live=False)
