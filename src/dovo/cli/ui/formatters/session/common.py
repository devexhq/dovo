"""Shared Rich tables and formatting helpers for session formatters."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.table import Table
from rich.text import Text

from dovo.cli.ui.formatters.session.session_views import SessionSummaryView
from dovo.common.utils import display_path, enum_value
from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.sessions import DiffResult, StepRunSummary

_SESSION_SHOW_FIELDS = (
    "Session ID",
    "Blueprint Name",
    "Branch",
    "Status",
    "Start time",
    "Completion time",
    "Duration",
)


def format_session_status(status: SessionStatus | str) -> str:
    """Format a run lifecycle status with canonical CLI coloring."""
    raw_status = enum_value(status).lower()
    if raw_status == SessionStatus.COMPLETED.value:
        return f"[green]{raw_status}[/green]"
    if raw_status == SessionStatus.PAUSED.value:
        return f"[yellow]{raw_status}[/yellow]"
    if raw_status == SessionStatus.FAILED.value:
        return f"[red]{raw_status}[/red]"
    if raw_status == SessionStatus.CANCELLED.value:
        return f"[dim]{raw_status}[/dim]"
    if raw_status == SessionStatus.RUNNING.value:
        return f"[cyan]{raw_status}[/cyan]"
    return raw_status


def format_session_duration(duration_seconds: float | None) -> str:
    """Format elapsed seconds into canonical human-readable CLI duration string."""
    if duration_seconds is None or duration_seconds < 0:
        return "-"
    if duration_seconds < 60:
        return f"{duration_seconds:.2f}s"

    minutes = int(duration_seconds // 60)
    seconds = duration_seconds % 60
    return f"{minutes}m {seconds:04.1f}s"


def format_step_duration(duration_seconds: float | None) -> str:
    """Format a step duration with one decimal and an 's' suffix; '<0.1s' under 0.1 seconds; '-' when missing."""
    if duration_seconds is None or duration_seconds < 0:
        return "-"
    if duration_seconds < 0.1:
        return "<0.1s"

    return f"{duration_seconds:.1f}s"


def build_session_summary(session: SessionRecord) -> SessionSummaryView:
    """Derive a presentation-ready SessionSummaryView from a SessionRecord domain model."""
    return SessionSummaryView(
        session_id=session.session_id,
        blueprint_name=session.blueprint_name,
        status=enum_value(session.status),
        branch_name=session.branch_name if session.branch_name else None,
        started_at=session.started_at,
        completed_at=session.completed_at,
        duration_seconds=session.duration_seconds,
        error_message=session.error_message,
    )


def build_session_table(sessions: Sequence[SessionSummaryView]) -> Table:
    """Build the Rich table displaying execution sessions."""
    table = Table(title="Execution History", title_justify="left", show_header=True)
    table.add_column("SESSION ID", style="cyan", no_wrap=True)
    table.add_column("BLUEPRINT")
    table.add_column("STATUS")
    table.add_column("STARTED")
    table.add_column("COMPLETED")
    table.add_column("DURATION", justify="right")

    for row in sessions:
        status_colored = format_session_status(row.status)
        duration = format_session_duration(row.duration_seconds)
        table.add_row(
            row.session_id,
            row.blueprint_name,
            status_colored,
            row.started_at or "-",
            row.completed_at or "-",
            duration,
        )
    return table


def build_metadata_table(session: SessionSummaryView) -> Table:
    """Build key/value table for session metadata."""
    duration = format_session_duration(session.duration_seconds)
    values = {
        "Session ID": session.session_id,
        "Blueprint Name": session.blueprint_name,
        "Branch": session.branch_name if session.branch_name else "-",
        "Status": format_session_status(session.status),
        "Start time": session.started_at or "-",
        "Completion time": session.completed_at or "-",
        "Duration": duration,
    }

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="bold")
    table.add_column()
    for field in _SESSION_SHOW_FIELDS:
        table.add_row(f"{field}:", values[field])
    return table


def unresolved_diff_path(session_id: str | None) -> str:
    """Return the placeholder diff path shown when no artifact path was resolved."""
    return f"<sessions_dir>/{session_id or '<session_id>'}/diff.patch"


def resolve_diff_rel_path(data: DiffResult, cwd: Path | None = None) -> str:
    """Resolve display path for diff artifact relative to current working directory."""
    effective_cwd = cwd or Path.cwd()
    if data.artifact_path is not None:
        return display_path(data.artifact_path, effective_cwd)
    return unresolved_diff_path(data.session_id)


def format_truncation_notice(
    session_id: str | None,
    relative_path: str,
    limit: int,
    total_lines: int,
) -> list[Any]:
    """Render truncation notice and interaction hints."""
    session_command = f"dovo diff {session_id}" if session_id else "dovo diff"
    return [
        Text(""),
        Text(f"... [diff truncated: showing {limit} of {total_lines} lines]", style="dim"),
        Text("Hint:", style="dim"),
        Text(f"- run `{session_command} --full` to view complete formatted output", style="dim"),
        Text(f"- run `{session_command} --full | less -R` to page through formatted diff", style="dim"),
        Text(f"- run `{session_command} --raw` to output unformatted patch text", style="dim"),
        Text(f"- inspect artifact at {relative_path}", style="dim"),
    ]


def build_steps_table(steps: Sequence[StepRunSummary]) -> Table:
    """Build the Steps table: columns #, Step, State, Attempts, Exit, Duration; body rows indented under their loop row."""
    table = Table(title="Steps", title_justify="left", show_header=True)
    table.add_column("#", justify="right")
    table.add_column("Step", no_wrap=True)
    table.add_column("State")
    table.add_column("Attempts", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")

    position = 0
    for row in steps:
        if row.loop_id is None:
            position += 1

        table.add_row(*_step_cells(row, position))
    return table


def _step_cells(row: StepRunSummary, position: int) -> tuple[str, ...]:
    """Return the table cells for one step row; body rows of a loop get no position and an indented label."""
    is_body_row = row.loop_id is not None
    return (
        "" if is_body_row else str(position),
        f"  {row.step_id} (iter {row.iteration})" if is_body_row else row.step_id,
        row.state,
        "-" if row.attempts is None else str(row.attempts),
        "-" if row.exit_code is None else str(row.exit_code),
        format_step_duration(row.duration_seconds),
    )


def build_step_error_lines(steps: Sequence[StepRunSummary]) -> list[Text]:
    """Return one '• <step id>: <message>' line per row that has an error_message."""
    return [Text(f"• {row.step_id}: {row.error_message}") for row in steps if row.error_message]
