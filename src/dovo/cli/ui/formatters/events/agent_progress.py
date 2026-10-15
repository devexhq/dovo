"""ComponentFormatter for AgentProgressEvent."""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field
from rich.console import Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from dovo.cli.ui.events import AgentProgressEvent
from dovo.common.types import ComponentFormatter
from dovo.core.agents import AgentProgressKind

_BULLET: Final[str] = "●"
_RESULT_CONNECTOR: Final[str] = "  ⎿"
_INLINE_CODE: Final[re.Pattern[str]] = re.compile(r"`([^`]+)`")
_VERBS: Final[dict[AgentProgressKind, str]] = {
    AgentProgressKind.EDIT: "Edited",
    AgentProgressKind.READ: "Read",
    AgentProgressKind.SEARCH: "Searched",
    AgentProgressKind.SHELL: "Ran",
}


class AgentProgressView(AgentProgressEvent):
    """Progress event plus the derived label and result lines; the derived fields never reach the JSON wire format."""

    verb: str = Field(default="", exclude=True)
    result_lines: list[str] = Field(default_factory=list, exclude=True)
    omitted_lines: int = Field(default=0, exclude=True)


def _result_lines(data: AgentProgressEvent) -> list[str]:
    """Return the result lines to show: a line count for a successful read, the first line for an edit, else the excerpt."""
    if data.call_kind is AgentProgressKind.READ and not data.is_error:
        return [f"Read {data.line_count} line{'' if data.line_count == 1 else 's'}"]

    lines = data.target.splitlines()
    if data.call_kind is AgentProgressKind.EDIT and not data.is_error:
        return lines[:1]

    return lines


def _shows_excerpt(data: AgentProgressEvent) -> bool:
    """Return whether the result shows output lines, so lines beyond the excerpt are counted as omitted."""
    summarized = data.call_kind in {AgentProgressKind.READ, AgentProgressKind.EDIT} and not data.is_error

    return data.kind is AgentProgressKind.RESULT and not summarized


def _inline_code_text(text: str) -> Text:
    """Return the text with each backtick span shown without its backticks in the inline-code style."""
    result = Text()
    position = 0
    for match in _INLINE_CODE.finditer(text):
        result.append(text[position : match.start()])
        result.append(match.group(1), style="magenta")
        position = match.end()

    result.append(text[position:])
    return result


def _target_text(kind: AgentProgressKind, target: str) -> Text:
    """Return a shell command highlighted as bash, or the target as plain text."""
    if kind is not AgentProgressKind.SHELL:
        return Text(target)

    highlighted = Syntax(target, "bash", theme="ansi_dark").highlight(target)
    highlighted.rstrip()
    return highlighted


def _hanging_entry(lead: Text, body: Text) -> Table:
    """Lay out a lead and a body in two columns so wrapped body lines stay indented under the body."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(lead, body)

    return grid


def _build_result(view: AgentProgressView) -> Table:
    """Lay out result lines under their action behind a connector, with an omitted-lines tail."""
    style = "red" if view.is_error else "dim"
    body = Text("\n").join(Text(line, style=style) for line in view.result_lines)
    if view.omitted_lines > 0:
        body.append(f"\n… +{view.omitted_lines} lines", style="dim")

    return _hanging_entry(Text(_RESULT_CONNECTOR, style="dim"), body)


class AgentProgressFormatter(ComponentFormatter[AgentProgressEvent, AgentProgressView]):
    """Formatter for agent actions, notes and tool results."""

    def transform(self, data: AgentProgressEvent) -> AgentProgressView:
        """Attach the verb and the result lines to show."""
        result_lines = _result_lines(data) if data.kind is AgentProgressKind.RESULT else []
        omitted_lines = max(0, data.line_count - len(result_lines)) if _shows_excerpt(data) else 0

        return AgentProgressView(
            **data.model_dump(),
            verb=_VERBS.get(data.kind, ""),
            result_lines=result_lines,
            omitted_lines=omitted_lines,
        )

    def to_rich(self, data: AgentProgressEvent) -> Group | Table:
        """Render a result under its action, or a note or action as a blank line and a bullet entry."""
        view = self.transform(data)
        if view.kind is AgentProgressKind.RESULT:
            return _build_result(view)

        bullet = Text(_BULLET, style="bold")
        if view.kind is AgentProgressKind.NOTE:
            return Group(Text(""), _hanging_entry(bullet, _inline_code_text(view.target)))

        body = Text.assemble((f"{view.verb} ", "bold"), _target_text(view.kind, view.target))

        return Group(Text(""), _hanging_entry(bullet, body))
