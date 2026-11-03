"""ComponentFormatter for DiffResult."""

from __future__ import annotations

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.session.common import (
    format_truncation_notice,
    resolve_diff_rel_path,
)
from dovo.cli.ui.formatters.session.session_views import SessionDiffView
from dovo.common.constants import DEFAULT_MAX_DIFF_LINES
from dovo.common.error_codes import ErrorCode
from dovo.core.sessions import DiffResult


def _panel_title(view: SessionDiffView) -> str:
    """Return the error panel title for the view's error_code."""
    if view.error_code == ErrorCode.SESSION_NOT_FOUND:
        return "Session Not Found"
    if view.error_code == ErrorCode.SESSION_DIFF_NOT_FOUND:
        return "Diff Not Found"
    if view.error_code == ErrorCode.SESSION_DIFF_UNREADABLE:
        return "Read Failure"
    return "Diff Failed"


def _format_diff_error_panel(view: SessionDiffView) -> Panel:
    """Format the error panel for a failed diff view, titled by its error_code."""
    return build_error_panel(_panel_title(view), view.errors, remediations=view.remediations)


class SessionDiffFormatter(ComponentFormatter[DiffResult, SessionDiffView]):
    """Formatter for diff command results."""

    def __init__(
        self,
        *,
        full: bool = False,
        max_lines: int | None = None,
        console: Console | None = None,
    ) -> None:
        """Initialize SessionDiffFormatter with optional truncation overrides.

        Args:
            full: Bypass line truncation limits.
            max_lines: Custom line truncation threshold.
            console: Optional Rich Console instance for terminal introspection.
        """
        self.full = full
        self.max_lines = max_lines
        self._console = console

    @property
    def console(self) -> Console:
        """Get the active Console instance."""
        if self._console is not None:
            return self._console
        return Console()

    def transform(self, data: DiffResult) -> SessionDiffView:
        """Derive the presentation-ready view from DiffResult domain data.

        Args:
            data: Raw DiffResult domain model.

        Returns:
            SessionDiffView containing resolved paths, line counts, and truncation flags.
        """
        relative_path = resolve_diff_rel_path(data)
        effective_max = data.max_lines if data.max_lines is not None else self.max_lines
        effective_full = data.full or self.full

        limit = effective_max if isinstance(effective_max, int) and effective_max > 0 else DEFAULT_MAX_DIFF_LINES
        diff_lines = data.diff_text.splitlines() if data.diff_text else []
        total_lines = len(diff_lines)
        is_terminal = self.console.is_terminal
        should_truncate = is_terminal and not effective_full and total_lines > limit

        return SessionDiffView(
            session_id=data.session_id,
            artifact_path=data.artifact_path,
            relative_path=relative_path,
            diff_text=data.diff_text,
            full=effective_full,
            max_lines=effective_max,
            latest=data.latest,
            total_lines=total_lines,
            truncated=should_truncate,
            truncated_lines=limit if should_truncate else 0,
            errors=list(data.errors),
            warnings=list(data.warnings),
            remediations=list(data.remediations),
            error_code=data.error_code,
            diagnostics=list(data.diagnostics),
        )

    def to_rich(self, data: DiffResult) -> RenderableType:
        """Render the error panel, the no-changes message for an empty diff, or the syntax-highlighted unified diff."""
        view = self.transform(data)

        if view.error_code is not None:
            return _format_diff_error_panel(view)

        if not view.diff_text:
            session_label = view.session_id or "unknown"
            return Text(f"No changes recorded for session {session_label}.")

        header_suffix = "latest" if view.latest else view.relative_path
        header = Text(f"Session: {view.session_id} ({header_suffix})\n")

        if view.truncated:
            diff_lines = view.diff_text.splitlines() if view.diff_text else []
            truncated_content = "\n".join(diff_lines[: view.truncated_lines])
            syntax = Syntax(truncated_content.rstrip(), "diff", word_wrap=True)
            notice_renderables = format_truncation_notice(
                view.session_id, view.relative_path, view.truncated_lines, view.total_lines
            )
            return Group(header, syntax, *notice_renderables)

        syntax = Syntax(view.diff_text.rstrip(), "diff", word_wrap=True)
        return Group(header, syntax)
