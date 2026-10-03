"""Live interactive display manager and renderable builders for Rich Live terminal execution."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dovo.cli.ui.events import (
    LoopConditionView,
    LoopLifecycleEvent,
    StepDoneEvent,
    StepOutputEvent,
    StepStartEvent,
    WorktreeLifecycleEvent,
)

DEFAULT_OUTPUT_BUFFER_SIZE = 8


@dataclass
class LiveStepItem:
    """State tracking model for single-step progress rows in the live table."""

    idx: int
    total: int
    name: str
    command: str | None
    status: str = "pending"  # "pending" | "running" | "completed" | "failed"
    start_time: float | None = None
    duration: float | None = None
    error_message: str | None = None


def _format_step_glyph(status: str) -> str:
    """Format status icon glyph for live table row."""
    if status == "running":
        return "[bold yellow]•[/bold yellow]"
    if status == "completed":
        return "[bold green]✔[/bold green]"
    if status == "failed":
        return "[bold red]✖[/bold red]"
    return "[dim]○[/dim]"


def _format_step_elapsed(item: LiveStepItem, now: float) -> str:
    """Format elapsed execution duration string for a live step item."""
    if item.status == "running" and item.start_time is not None:
        return f"{now - item.start_time:.1f}s"
    if item.duration is not None:
        return f"{item.duration:.2f}s"
    return "-"


def _resolve_step_duration(item: LiveStepItem, duration_seconds: float | None, now: float) -> float | None:
    """Resolve elapsed duration when step concludes."""
    if item.start_time is not None:
        return max(0.0, now - item.start_time)
    return duration_seconds


def _format_failure_detail(event: StepDoneEvent) -> str:
    """Format failure detail string for failed step row."""
    return event.error_message or f"Command failed with exit code {event.exit_code}."


def build_live_step_table(
    steps: list[LiveStepItem],
    *,
    worktree_info: str | None = None,
    now: float | None = None,
) -> Table:
    """Build the Rich table displaying dynamic step execution progress."""
    title = f"Blueprint Execution Progress ({worktree_info})" if worktree_info else "Blueprint Execution Progress"
    table = Table(title=title, title_justify="left", show_header=True)
    table.add_column("Status", width=6, justify="center")
    table.add_column("Step")
    table.add_column("Command")
    table.add_column("Elapsed", justify="right")

    current_time = now if now is not None else time.monotonic()
    for item in steps:
        glyph = _format_step_glyph(item.status)
        elapsed = _format_step_elapsed(item, current_time)
        cmd_display = item.command or "[dim]-[/dim]"
        step_label = f"[{item.idx}/{item.total}] {item.name}"
        table.add_row(glyph, step_label, cmd_display, elapsed)

    return table


def build_live_output_panel(
    step_name: str,
    lines: Sequence[str],
) -> Panel:
    """Build a framed Rich Panel displaying buffered live step output lines."""
    body_text = Text.from_ansi("\n".join(lines)) if lines else Text("")
    return Panel(body_text, title=f"Output: {step_name}", title_align="left")


def _format_iteration_marker(
    iteration_number: int, current_iteration: int | None, iteration_results: Mapping[int, bool]
) -> Text:
    """Format a single iteration-history marker: pass, fail, or pending, current iteration distinguished."""
    result = iteration_results.get(iteration_number)
    if result is True:
        marker = Text(f"{iteration_number} ✔", style="bold green")
    elif result is False:
        marker = Text(f"{iteration_number} ✖", style="bold red")
    else:
        marker = Text(f"{iteration_number} ○", style="dim")
    if iteration_number == current_iteration:
        marker.stylize("underline")
    return marker


def build_loop_status_panel(
    loop_id: str,
    iteration: int | None,
    max_iterations: int | None,
    conditions: Sequence[LoopConditionView],
    iteration_results: Mapping[int, bool],
) -> Panel:
    """Build the Rich Panel showing current loop/iteration status and iteration history."""
    lines: list[Text] = []

    header = Text(loop_id, style="bold cyan")
    if iteration is not None and max_iterations is not None:
        header.append(f"  iteration {iteration}/{max_iterations}", style="dim")
    lines.append(header)

    for condition in conditions:
        detail_line = Text("until: ")
        detail_line.append(condition.expression)
        detail_line.append(" → ")
        outcome = condition.detail or ("passed" if condition.passed else "failed")
        detail_line.append(outcome, style="bold green" if condition.passed else "bold red")
        lines.append(detail_line)

    if max_iterations is not None:
        strip = Text("iterations  ")
        for iteration_number in range(1, max_iterations + 1):
            if iteration_number > 1:
                strip.append("  ")
            strip.append_text(_format_iteration_marker(iteration_number, iteration, iteration_results))
        lines.append(strip)

    return Panel(Group(*lines), title="Loop", title_align="left")


def build_live_renderable(
    steps: list[LiveStepItem],
    *,
    active_step_name: str | None = None,
    output_lines: Sequence[str] | None = None,
    worktree_info: str | None = None,
    loop_panel: Panel | None = None,
) -> Table | Group:
    """Build the composite Rich renderable with loop status, step table, and optional output panel."""
    table = build_live_step_table(steps, worktree_info=worktree_info)
    body: Table | Group = table
    if active_step_name is not None:
        panel = build_live_output_panel(active_step_name, output_lines or [])
        body = Group(table, Text(""), panel)
    if loop_panel is None:
        return body
    return Group(loop_panel, Text(""), body)


class LiveDisplayManager:
    """Manager coordinating dynamic Rich Live rendering for interactive terminal runs."""

    def __init__(
        self,
        console: Console,
        *,
        output_buffer_size: int = DEFAULT_OUTPUT_BUFFER_SIZE,
    ) -> None:
        """Initialize live display manager.

        Args:
            console: Rich Console instance to bind the Live display to.
            output_buffer_size: Maximum lines to retain in the active output panel ring buffer.
        """
        self.console = console
        self.worktree_info: str | None = None
        self.steps: list[LiveStepItem] = []
        self.output_buffer_size = output_buffer_size
        self._active_step_name: str | None = None
        self._active_output: deque[str] = deque(maxlen=output_buffer_size)
        self._live: Live | None = None
        self._loop_id: str | None = None
        self._loop_iteration: int | None = None
        self._loop_max_iterations: int | None = None
        self._loop_conditions: list[LoopConditionView] = []
        self._iteration_results: dict[int, bool] = {}

    @property
    def is_active(self) -> bool:
        """Whether the Live display context is currently started."""
        return self._live is not None

    def start(self) -> None:
        """Start the Rich Live display session."""
        if self._live is not None:
            return
        self._live = Live(
            self._build_renderable(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )
        self._live.__enter__()

    def stop(self) -> None:
        """Stop the Rich Live display session and render final table state."""
        if self._live is None:
            return
        self._active_step_name = None
        self._active_output.clear()
        self._live.update(self._build_renderable())
        self._live.__exit__(None, None, None)
        self._live = None

    def handle_step_start(self, event: StepStartEvent) -> None:
        """Record the start of a step and refresh live table."""
        step_label = event.name or event.step_id
        now = time.monotonic()
        self._active_step_name = step_label
        self._active_output.clear()
        item = LiveStepItem(
            idx=event.idx,
            total=event.total,
            name=step_label,
            command=event.command,
            status="running",
            start_time=now,
        )
        self.steps.append(item)
        self._refresh()

    def handle_step_output(self, event: StepOutputEvent) -> None:
        """Buffer live step output line and refresh output panel."""
        self._active_step_name = event.step_id
        self._active_output.append(event.line.rstrip("\r\n"))
        self._refresh()

    def handle_step_done(self, event: StepDoneEvent) -> None:
        """Record step completion and refresh live table."""
        if self.steps:
            current = self.steps[-1]
            current.status = "completed" if event.ok else "failed"
            current.duration = _resolve_step_duration(current, event.duration_seconds, time.monotonic())
            if not event.ok:
                current.error_message = _format_failure_detail(event)
        self._active_step_name = None
        self._active_output.clear()
        self._refresh()

    def handle_loop_lifecycle(self, event: LoopLifecycleEvent) -> None:
        """Update tracked loop/iteration state from a loop lifecycle event and refresh."""
        if event.action == "start":
            self._loop_id = event.loop_id
            self._loop_max_iterations = event.max_iterations
        elif event.action == "iteration_start":
            self._loop_iteration = event.iteration
            self._loop_conditions = []
            self.steps = []
        elif event.action == "conditions_evaluated":
            self._loop_conditions = event.conditions
            if self._loop_iteration is not None:
                self._iteration_results[self._loop_iteration] = all(condition.passed for condition in event.conditions)
        self._refresh()

    def handle_worktree(self, event: WorktreeLifecycleEvent, rendered: Text) -> None:
        """Handle worktree lifecycle event by updating title info and printing above live table."""
        if event.action == "ready":
            self.worktree_info = f"Active ({event.path})" if event.active else "In-place (workspace)"
        self.print_above(rendered)
        self._refresh()

    def print_above(self, renderable: Any) -> None:
        """Print a renderable above the live display without breaking progress."""
        if self._live is not None:
            self._live.console.print(renderable)
        else:
            self.console.print(renderable)

    def _build_renderable(self) -> Table | Group:
        """Build the combined renderable containing loop status, steps table, active output, and worktree info."""
        loop_panel = (
            build_loop_status_panel(
                self._loop_id,
                self._loop_iteration,
                self._loop_max_iterations,
                self._loop_conditions,
                self._iteration_results,
            )
            if self._loop_id is not None
            else None
        )
        return build_live_renderable(
            self.steps,
            active_step_name=self._active_step_name,
            output_lines=list(self._active_output),
            worktree_info=self.worktree_info,
            loop_panel=loop_panel,
        )

    def _refresh(self) -> None:
        """Update the active Live display instance with latest renderables."""
        if self._live is not None:
            self._live.update(self._build_renderable())
