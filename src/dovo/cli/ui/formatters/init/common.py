"""Shared formatting helpers for workspace initialization."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.formatters.common import (
    ERROR_PANEL_STYLE,
    render_list_errors,
    render_list_fixes,
)
from dovo.cli.ui.formatters.init.init_view import WorkspaceInitView
from dovo.core.bootstrap.models import BootstrapOutcome, InitFailureMode


def render_string_bullets(items: list[str], label: str) -> list[Any]:
    """Render bullet list of relative path strings or keys."""
    lines: list[Any] = [Text.from_markup(f"[bold dim]{label}:[/bold dim]")]
    for item in items:
        lines.append(Text.from_markup(f"  [dim]•[/dim] [cyan]{item}[/cyan]"))
    return lines


def render_bootstrap_lines(view: WorkspaceInitView) -> list[Any]:
    """Render bootstrap result status and created directory lines."""
    renderables: list[Any] = []
    dovo_label = view.root_path_relative or ".dovo"
    if view.bootstrap_outcome == BootstrapOutcome.REPAIRED:
        renderables.append(Text.from_markup(f"[bold green]✔  Dovo structure repaired at {dovo_label}[/bold green]"))
        renderables.extend(render_string_bullets(view.dirs_created, "Created missing"))
    elif view.bootstrap_outcome == BootstrapOutcome.INITIALIZED:
        renderables.append(Text.from_markup(f"[bold green]✔  Initialized Dovo at {dovo_label}[/bold green]"))
        renderables.extend(render_string_bullets(view.dirs_created, "Created"))
    elif view.bootstrap_outcome == BootstrapOutcome.ALREADY_INITIALIZED:
        renderables.append(Text.from_markup(f"[bold green]✔  Dovo already initialized at {dovo_label}[/bold green]"))
        renderables.append(Text.from_markup("[bold dim]No changes required.[/bold dim]"))
    return renderables


def render_identity_lines(view: WorkspaceInitView) -> list[Any]:
    """Render project identity and local .dovo/.gitignore result lines."""
    renderables: list[Any] = []
    if view.project_id is not None:
        identity_label = view.identity_path_relative or ".dovo/project.json"
        renderables.append(Text.from_markup(f"  [dim]•[/dim] Project ID: [cyan]{view.project_id}[/cyan]"))
        renderables.append(Text.from_markup(f"  [dim]•[/dim] Identity: [cyan]{identity_label}[/cyan]"))
        if view.identity_preserved and view.warnings:
            renderables.append(Text.from_markup(f"[bold yellow]  {view.warnings[0]}[/bold yellow]"))

    if view.gitignore_path_relative is not None:
        tracked_entries = ", ".join(view.gitignore_tracked_entries)
        renderables.append(
            Text.from_markup(
                f"  [dim]•[/dim] Gitignore populated at {view.gitignore_path_relative}; "
                f"tracking only {tracked_entries}."
            )
        )
    return renderables


def render_config_lines(view: WorkspaceInitView) -> list[Any]:
    """Render config generation result lines."""
    if not view.config_path_relative:
        return []
    renderables: list[Any] = [Text("")]
    label = f"./{view.config_path_relative}"
    if view.config_created:
        renderables.append(Text.from_markup(f"  [dim]•[/dim] Generated config: [cyan]{label}[/cyan]"))
    elif view.config_overwritten:
        renderables.append(Text.from_markup(f"  [dim]•[/dim] Regenerated config: [cyan]{label}[/cyan]"))
    elif view.config_repaired:
        renderables.append(Text.from_markup(f"  [dim]•[/dim] Repaired config: [cyan]{label}[/cyan]"))
        if view.inserted_keys:
            renderables.append(Text.from_markup("[bold dim]  Added missing keys:[/bold dim]"))
            for key in view.inserted_keys:
                renderables.append(Text.from_markup(f"  [dim]•[/dim] [cyan]{key}[/cyan]"))
    elif view.config_skipped_existing:
        renderables.append(Text.from_markup(f"  [dim]•[/dim] Config exists: [cyan]{label}[/cyan]"))
    return renderables


def render_seed_lines(view: WorkspaceInitView) -> list[Any]:
    """Render template seed result lines."""
    renderables: list[Any] = [Text("")]
    if view.seeded_files:
        renderables.append(Text.from_markup("[bold green]✔  Seeded starter blueprints[/bold green]"))
        renderables.extend(render_string_bullets(view.seeded_files, "Created"))
    elif view.overwritten_seed_files:
        renderables.append(Text.from_markup("[bold green]✔  Refreshed starter blueprints[/bold green]"))
    else:
        renderables.append(Text.from_markup("[bold green]✔  Starter blueprints already present[/bold green]"))

    if view.skipped_seed_files:
        renderables.extend(render_string_bullets(view.skipped_seed_files, "Skipped existing"))

    if view.errors and view.failure_mode is None:
        lines = "\n".join(f"- {error}" for error in view.errors)
        renderables.append(
            Panel.fit(
                f"[bold red]Starter blueprint seeding failed:[/bold red]\n{lines}",
                border_style="red",
            )
        )
    return renderables


def render_preflight_failure(view: WorkspaceInitView) -> Panel:
    """Render panel when initialization preflight checks fail."""
    lines = render_list_errors(view.errors, separator="\n")
    if fixes_message := render_list_fixes(view.fixes, bullet="  "):
        lines = f"{lines}\n{fixes_message}"
    return Panel.fit(f"[bold red]Initialization Failed![/bold red]\n{lines}", border_style=ERROR_PANEL_STYLE)


def render_bootstrap_failure(view: WorkspaceInitView) -> Panel:
    """Render panel when bootstrap step fails."""
    lines = "\n".join(f"  {error}" for error in view.errors)
    fixes = view.fixes or ["Resolve the path conflict above, then rerun [bold cyan]dovo init[/bold cyan]."]
    remediation = render_list_fixes(fixes, bullet="  ")
    return Panel.fit(
        f"[bold red]Failed to initialize Dovo:[/bold red]\n{lines}\n{remediation}",
        border_style=ERROR_PANEL_STYLE,
    )


def render_config_failure(view: WorkspaceInitView) -> Panel:
    """Render panel when configuration generation fails."""
    lines = "\n".join(f"- {error}" for error in view.errors)
    if fixes_message := render_list_fixes(view.fixes, bullet="  "):
        lines = f"{lines}\n{fixes_message}"
    return Panel.fit(f"[bold red]Failed to generate config:[/bold red]\n{lines}", border_style=ERROR_PANEL_STYLE)


def render_failure_panel(view: WorkspaceInitView) -> Panel | None:
    """Render failure panels for preflight, bootstrap, or configuration generation errors."""
    if view.failure_mode == InitFailureMode.PREFLIGHT:
        return render_preflight_failure(view)
    if view.failure_mode in (InitFailureMode.BOOTSTRAP, InitFailureMode.INVALID_PROJECT_ID):
        return render_bootstrap_failure(view)
    if view.failure_mode == InitFailureMode.CONFIG_GENERATION:
        return render_config_failure(view)
    return None
