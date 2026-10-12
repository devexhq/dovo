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
        if data.prompt_type == "interaction":
            return self._interaction_renderables(data)

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

    def _interaction_renderables(self, data: PromptEvent) -> Group:
        """Lay out the header with artifact reference, preview lines, numbered options and comment hint without deriving values."""
        header = Text(f"[{data.step_id}] ", style="bold cyan")
        header.append(data.title, style="bold")
        if data.artifact is not None:
            header.append(f"   ({data.artifact.reference})", style="dim")

        renderables: list[Text] = [header]
        if data.artifact is not None:
            renderables.extend(Text(f"  {line}", style="dim") for line in data.artifact.preview)

        for option in data.options:
            renderables.append(Text(f"  {option.key}) {option.display or option.label}"))

        if data.comment is not None and data.comment.policy != "disabled":
            renderables.append(Text(f"{data.comment.message} ({data.comment.policy})", style="dim"))
        renderables.append(Text(""))

        return Group(*renderables)
