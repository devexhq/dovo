"""ComponentFormatter for ErrorEvent, the one report of a failure no command result carries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from rich.console import Group, RenderableType

from dovo.cli.ui.events import ErrorEvent
from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel, diagnostic_lines
from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.models import Remediation


class ErrorEventView(BaseModel):
    """The failed-result header of the `error` event; the panel title is not part of the payload."""

    model_config = {"extra": "forbid", "strict": True}

    title: str = Field(exclude=True)
    ok: Literal[False] = False
    error_code: str
    errors: list[str]
    warnings: list[str] = Field(default_factory=list)
    remediations: list[Remediation] = Field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = Field(default_factory=list)


class ErrorEventFormatter(ComponentFormatter[ErrorEvent, ErrorEventView]):
    """Formatter for a failure no command result carries."""

    def transform(self, data: ErrorEvent) -> ErrorEventView:
        """Derive the view: the panel title, then the header with ok false and no warnings."""
        return ErrorEventView(
            title=data.title,
            error_code=data.error_code,
            errors=list(data.errors),
            remediations=list(data.remediations),
            diagnostics=list(data.diagnostics),
        )

    def to_rich(self, data: ErrorEvent) -> RenderableType:
        """Render the titled panel with the errors and the Fix: block, then diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        panel = build_error_panel(view.title, view.errors, remediations=view.remediations)
        return Group(panel, *diagnostic_lines(view.diagnostics))
