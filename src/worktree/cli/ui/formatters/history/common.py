"""Shared Rich tables and formatting helpers for history formatters."""

from __future__ import annotations

from collections.abc import Sequence

from rich.table import Table

from worktree.cli.ui.formatters.history.history_views import RunSummaryView
from worktree.common.utils import enum_value
from worktree.core.db import RunRecord, RunStatus

_SESSION_SHOW_FIELDS = (
    "Session ID",
    "Blueprint Name",
    "Branch",
    "Status",
    "Start time",
    "Completion time",
    "Duration",
)


def format_run_status(status: RunStatus | str) -> str:
    """Format a run lifecycle status with canonical CLI coloring."""
    raw_status = enum_value(status).lower()
    if raw_status == RunStatus.COMPLETED.value:
        return f"[green]{raw_status}[/green]"
    if raw_status == RunStatus.PAUSED.value:
        return f"[yellow]{raw_status}[/yellow]"
    if raw_status == RunStatus.FAILED.value:
        return f"[red]{raw_status}[/red]"
    if raw_status == RunStatus.CANCELLED.value:
        return f"[dim]{raw_status}[/dim]"
    if raw_status == RunStatus.RUNNING.value:
        return f"[cyan]{raw_status}[/cyan]"
    return raw_status


def format_run_duration(duration_seconds: float | None) -> str:
    """Format elapsed seconds into canonical human-readable CLI duration string."""
    if duration_seconds is None or duration_seconds < 0:
        return "-"
    if duration_seconds < 60:
        return f"{duration_seconds:.2f}s"

    minutes = int(duration_seconds // 60)
    seconds = duration_seconds % 60
    return f"{minutes}m {seconds:04.1f}s"


def build_run_summary(run: RunRecord) -> RunSummaryView:
    """Derive a presentation-ready RunSummaryView from a RunRecord domain model."""
    return RunSummaryView(
        session_id=run.session_id,
        blueprint_name=run.blueprint_name,
        status=enum_value(run.status),
        branch_name=run.branch_name if run.branch_name else None,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_seconds=run.duration_seconds,
        error_message=run.error_message,
    )


def build_history_table(runs: Sequence[RunSummaryView]) -> Table:
    """Build the Rich table displaying execution history runs."""
    table = Table(title="Execution History", title_justify="left", show_header=True)
    table.add_column("SESSION ID", style="cyan", no_wrap=True)
    table.add_column("BLUEPRINT")
    table.add_column("STATUS")
    table.add_column("STARTED")
    table.add_column("COMPLETED")
    table.add_column("DURATION", justify="right")

    for row in runs:
        status_colored = format_run_status(row.status)
        duration = format_run_duration(row.duration_seconds)
        table.add_row(
            row.session_id,
            row.blueprint_name,
            status_colored,
            row.started_at or "-",
            row.completed_at or "-",
            duration,
        )
    return table


def build_metadata_table(run: RunSummaryView) -> Table:
    """Build key/value table for session metadata."""
    duration = format_run_duration(run.duration_seconds)
    values = {
        "Session ID": run.session_id,
        "Blueprint Name": run.blueprint_name,
        "Branch": run.branch_name if run.branch_name else "-",
        "Status": format_run_status(run.status),
        "Start time": run.started_at or "-",
        "Completion time": run.completed_at or "-",
        "Duration": duration,
    }

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="bold")
    table.add_column()
    for field in _SESSION_SHOW_FIELDS:
        table.add_row(f"{field}:", values[field])
    return table
