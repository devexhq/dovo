"""ComponentFormatter for WelcomeBannerEvent."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from dovo.cli.ui.events import WelcomeBannerEvent
from dovo.common.types import ComponentFormatter


class WelcomeBannerFormatter(ComponentFormatter[WelcomeBannerEvent]):
    """Formatter for welcome brand banner."""

    def to_rich(self, data: WelcomeBannerEvent) -> Panel:
        """Render ASCII brand banner."""
        banner_text = Text()
        banner_text.append("🌳 Dovo CLI ", style="bold green")
        banner_text.append(f"v{data.version}\n", style="dim cyan")
        banner_text.append("Isolated Git Workspaces & Agent Workflows", style="italic dim")
        return Panel(banner_text, border_style="green", expand=False, padding=(1, 4))
