"""ComponentFormatter for BlueprintRunResult, the last event of every run and resume."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import SUCCESS_STYLE, WARNING_STYLE, build_error_panel, diagnostic_lines
from dovo.cli.ui.formatters.header import header_fields
from dovo.cli.ui.formatters.run.run_views import RunResultView
from dovo.common.error_codes import ErrorCode
from dovo.core.db import SessionStatus
from dovo.engine.models import BlueprintRunResult


def _render_outcome(view: RunResultView) -> RenderableType:
    """Render the Run Cancelled or Run Failed panel for a failure, else the paused or completed line."""
    if view.error_code == ErrorCode.RUN_CANCELLED:
        return build_error_panel("Run Cancelled", view.errors, remediations=view.remediations)
    if view.error_code is not None:
        return build_error_panel("Run Failed", view.errors, remediations=view.remediations)
    if view.session_status is SessionStatus.PAUSED or view.paused_prompt_id is not None:
        return Text("Blueprint paused; checkpoint saved.")

    return Text.assemble(
        ("Blueprint Run Completed:", f"bold {SUCCESS_STYLE}"),
        f" {view.blueprint_name or 'unknown'} (session: {view.session_id or 'unknown'}, status: completed)",
    )


class RunResultFormatter(ComponentFormatter[BlueprintRunResult, RunResultView]):
    """Formatter for the run result that ends every `dovo run` and `dovo resume`."""

    event_type: ClassVar[str] = "run.result"

    def transform(self, data: BlueprintRunResult) -> RunResultView:
        """Derive the view: the shared header, with ok from error_code, then the session fields."""
        return RunResultView(
            **header_fields(data),
            session_id=data.session_id,
            blueprint_name=data.blueprint_name,
            session_status=data.session_status,
            paused_prompt_id=data.paused_prompt_id,
        )

    def to_rich(self, data: BlueprintRunResult) -> RenderableType:
        """Render one yellow Warning: line per warning, then the outcome, then diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        warnings = [Text.assemble(("Warning:", WARNING_STYLE), f" {warning}") for warning in view.warnings]
        return Group(*warnings, _render_outcome(view), *diagnostic_lines(view.diagnostics))
