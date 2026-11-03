"""Shared formatting helpers and tables for status formatters."""

from __future__ import annotations

from rich.table import Table

from dovo.cli.ui.formatters.status.status_view import StatusHealth, StatusView


def get_table_title(view: StatusView) -> str:
    """Determine the status table title based on workspace health."""
    if view.health == StatusHealth.UNINITIALIZED:
        return "Dovo Workspace Status (Uninitialized)"
    if view.health == StatusHealth.DEGRADED:
        return "Dovo Workspace Status (Degraded)"
    return "Dovo Workspace Status"


def format_project_name(view: StatusView) -> str:
    """Format project name with uninitialized fallback."""
    if view.project_name:
        return view.project_name
    if view.config_error_code is None:
        return "[dim]unnamed_project[/dim]"
    if view.health != StatusHealth.UNINITIALIZED:
        return "[dim]unknown (invalid config)[/dim]"
    return "[dim]Uninitialized[/dim]"


def format_config_status(view: StatusView) -> str:
    """Format the config cell: ok with the relative path, or the load's error code, yellow when uninitialized."""
    if view.config_error_code is None:
        return f"ok ({view.config_path_relative})"
    colour = "yellow" if view.health == StatusHealth.UNINITIALIZED else "red"
    return f"[{colour}]{view.config_error_code}[/{colour}]"


def format_git_branch(view: StatusView) -> str:
    """Format active git branch or non-git status badge."""
    if view.git_branch is None:
        return "[yellow]NOT_A_GIT_REPO[/yellow]"
    if view.git_is_dirty:
        return f"[yellow]{view.git_branch} (dirty)[/yellow]"
    return view.git_branch


def format_agent_model(view: StatusView) -> str:
    """Format configured agent model or unconfigured fallback."""
    if view.agent_model is not None:
        return view.agent_model
    return "[dim]Not Configured[/dim]"


def format_worktrees_status(view: StatusView) -> str:
    """Format active worktrees count or N/A when config is unavailable."""
    if view.active_worktrees is None or view.max_active_worktrees is None:
        return "[dim]N/A[/dim]"
    return f"{view.active_worktrees} / {view.max_active_worktrees} max"


def format_catalog_status(view: StatusView) -> str:
    """Format catalog item counts or N/A when config is unavailable."""
    if view.valid_catalog_items is None or view.total_catalog_items is None:
        return "[dim]N/A[/dim]"
    return f"{view.valid_catalog_items} valid / {view.total_catalog_items} total"


def build_status_table(view: StatusView) -> Table:
    """Build Rich Table representing workspace status summary."""
    table = Table(title=get_table_title(view), title_justify="left", show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="bold green")

    table.add_row("Project Name", format_project_name(view))
    table.add_row("Config Status", format_config_status(view))
    table.add_row("Active Git Branch", format_git_branch(view))
    table.add_row("Agent Model", format_agent_model(view))
    table.add_row("Active Worktrees", format_worktrees_status(view))
    table.add_row("Catalog Items", format_catalog_status(view))
    return table
