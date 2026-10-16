"""ComponentFormatter for StepOutputEvent."""

from __future__ import annotations

from rich.console import Group
from rich.padding import Padding
from rich.text import Text

from dovo.cli.ui.events import AgentSummaryView, StepOutputEvent
from dovo.common.types import ComponentFormatter


def _build_agent_summary(summary: AgentSummaryView) -> Group:
    """Lay out an agent summary as an indented block set off by blank lines."""
    status_style = "green" if summary.succeeded else "red"
    lines: list[Text | Padding] = [
        Text(""),
        Text.assemble(("  Result: ", "bold"), (summary.status, status_style)),
    ]
    if summary.unfixable_reason:
        lines.append(Padding(Text(f"Reason: {summary.unfixable_reason}"), (0, 0, 0, 2)))
    if summary.summary:
        lines.append(Padding(Text(summary.summary), (0, 0, 0, 2)))
    if summary.touched_files:
        lines.append(Text(""))
        lines.append(Text("  Touched files:"))
        lines.extend(Text(f"    {path}") for path in summary.touched_files)
    lines.append(Text(""))

    return Group(*lines)


class StepOutputFormatter(ComponentFormatter[StepOutputEvent, StepOutputEvent]):
    """Formatter for live step output lines."""

    def to_rich(self, data: StepOutputEvent) -> Text | Group:
        """Render raw output text, or the agent summary block."""
        view = self.transform(data)
        if view.agent_summary is None:
            return Text(view.line)

        return _build_agent_summary(view.agent_summary)
