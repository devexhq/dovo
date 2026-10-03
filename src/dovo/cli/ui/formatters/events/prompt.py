"""ComponentFormatter for PromptEvent."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.events import PromptEvent
from dovo.common.types import ComponentFormatter


class PromptFormatter(ComponentFormatter[PromptEvent]):
    """Formatter for interactive prompt events."""

    def to_rich(self, data: PromptEvent) -> Group:
        """Render prompt title, diagnostic, status, and options as formatted Rich renderables."""
        renderables: list[Text] = []

        renderables.append(Text.from_markup(f"[bold red]{data.title}[/bold red]"))
        if data.diagnostic:
            renderables.append(Text.from_markup(f"[dim]{data.diagnostic}[/dim]"))

        renderables.append(Text(""))
        renderables.append(Text.from_markup("[yellow]Blueprint paused waiting for user input.[/yellow]"))
        renderables.append(Text(""))
        renderables.append(Text.from_markup("[bold]Options:[/bold]"))
        for opt in data.options:
            renderables.append(Text.from_markup(f"  \\[[bold cyan]{opt.key}[/bold cyan]] {opt.label}"))
        renderables.append(Text(""))

        return Group(*renderables)
