"""Shared formatting helpers and tables for status formatters."""

from __future__ import annotations

from rich.table import Table

from dovo.cli.ui.formatters.status.status_view import StatusHealth, StatusView
from dovo.core.config.loader import ConfigLoadStatus

CONFIG_STATUS_DISPLAY: dict[ConfigLoadStatus, str] = {
    ConfigLoadStatus.OK: "ok",
    ConfigLoadStatus.NOT_FOUND: "[yellow]CONFIG_NOT_FOUND[/yellow]",
    ConfigLoadStatus.MALFORMED_JSON: "[red]CONFIG_MALFORMED_JSON[/red]",
    ConfigLoadStatus.SCHEMA_INVALID: "[red]CONFIG_SCHEMA_INVALID[/red]",
    ConfigLoadStatus.ROOT_NOT_OBJECT: "[red]CONFIG_ROOT_NOT_OBJECT[/red]",
    ConfigLoadStatus.PATH_IS_DIRECTORY: "[red]PATH_IS_DIRECTORY[/red]",
    ConfigLoadStatus.UNREADABLE: "[red]CONFIG_UNREADABLE[/red]",
}


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
    if view.config_status == ConfigLoadStatus.OK:
        return "[dim]unnamed_project[/dim]"
    if view.health != StatusHealth.UNINITIALIZED:
        return "[dim]unknown (invalid config)[/dim]"
    return "[dim]Uninitialized[/dim]"


def format_config_status(view: StatusView) -> str:
    """Format config status with color-coded codes or relative path."""
    if view.config_status == ConfigLoadStatus.OK:
        return f"ok ({view.config_path_relative})"
    return CONFIG_STATUS_DISPLAY.get(
        view.config_status,
        f"[red]{view.config_status.value.upper()}[/red]",
    )


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


def format_sandboxes_status(view: StatusView) -> str:
    """Format active sandboxes count or N/A when config is unavailable."""
    if view.active_sandboxes is None or view.max_active_sandboxes is None:
        return "[dim]N/A[/dim]"
    return f"{view.active_sandboxes} / {view.max_active_sandboxes} max"


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
    table.add_row("Active Sandboxes", format_sandboxes_status(view))
    table.add_row("Catalog Items", format_catalog_status(view))
    return table
