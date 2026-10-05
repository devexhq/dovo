"""Shared Rich tables and formatting helpers for history formatters."""

from __future__ import annotations

from collections.abc import Sequence

from rich.table import Table

from dovo.cli.ui.formatters.history.history_views import SessionSummaryView
from dovo.common.utils import enum_value
from dovo.core.db import SessionRecord, SessionStatus

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


def build_history_table(sessions: Sequence[SessionSummaryView]) -> Table:
    """Build the Rich table displaying execution history sessions."""
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
