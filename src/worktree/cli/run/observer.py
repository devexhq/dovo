"""CLI RunObserver implementations routing execution lifecycle events to UiDispatcher."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from worktree.cli.ui.dispatcher import UiDispatcher
from worktree.cli.ui.events import (
    LoopConditionView,
    LoopLifecycleEvent,
    SandboxLifecycleEvent,
    StepDoneEvent,
    StepOutputEvent,
    StepStartEvent,
)
from worktree.common.models import DisplayFormatOptions, OutputFormatOptions
from worktree.core.engine.models import RunObserver
from worktree.core.step import ConditionEvaluationResult, StepDefinition, StepResult

if TYPE_CHECKING:
    from types import TracebackType


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

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        """Dispatch sandbox readiness event."""
        self._dispatcher.dispatch(SandboxLifecycleEvent(action="ready", path=str(path), active=active))

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
        self._dispatcher.dispatch(
            StepOutputEvent(
                step_id=step.id,
                line=line.rstrip("\r\n"),
                stream=stream,
            )
        )

    def on_step_done(self, idx: int, total: int, result: StepResult) -> None:
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

    def on_loop_turn_start(self, loop_id: str, turn: int, max_iterations: int) -> None:
        """Dispatch loop turn start event."""
        self._dispatcher.dispatch(
            LoopLifecycleEvent(
                loop_id=loop_id,
                action="turn_start",
                turn=turn,
                max_iterations=max_iterations,
            )
        )

    def on_loop_conditions_evaluated(
        self,
        loop_id: str,
        results: list[ConditionEvaluationResult],
        all_passed: bool,
        next_turn: int | None = None,
    ) -> None:
        """Dispatch loop until conditions evaluated event."""
        lines = [f"\\[{loop_id}] Evaluated 'until' conditions:"]
        for r in results:
            lines.append(f"  - {r.expression}: {r.detail}")
        if not all_passed and next_turn is not None:
            lines.append(f"\\[{loop_id}] Conditions not met. Continuing to turn {next_turn}...")
        self._dispatcher.dispatch(
            LoopLifecycleEvent(
                loop_id=loop_id,
                action="conditions_evaluated",
                message="\n".join(lines),
                conditions=[
                    LoopConditionView(expression=r.expression, passed=r.passed, detail=r.detail) for r in results
                ],
                next_turn=next_turn,
            )
        )

    def on_loop_done(self, loop_id: str, status: str, turns: int) -> None:
        """Dispatch loop completion event."""
        self._dispatcher.dispatch(
            LoopLifecycleEvent(
                loop_id=loop_id,
                action="done",
                turn=turns,
                status=status,
            )
        )

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        """Dispatch sandbox cleanup event."""
        self._dispatcher.dispatch(SandboxLifecycleEvent(action="cleanup", path=str(path), kept=kept))


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
