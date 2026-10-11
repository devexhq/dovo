"""ComponentFormatter for DiffResult."""

from __future__ import annotations

from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from dovo.cli.ui.formatters.common import build_error_panel
from dovo.cli.ui.formatters.diff.common import (
    format_truncation_notice,
    resolve_diff_rel_path,
    unresolved_diff_path,
)
from dovo.cli.ui.formatters.diff.diff_view import DiffResultView
from dovo.common.constants import DEFAULT_MAX_DIFF_LINES
from dovo.common.types import ComponentFormatter
from dovo.core.sessions import DiffResult, DiffStatus


def _format_session_not_found_panel(data: DiffResult | DiffResultView, *, raw: bool = False) -> Panel | str:
    """Format error panel when session is missing."""
    default = f"Session '{data.session_id}' not found." if data.session_id else "No sessions found."
    fixes = data.fixes or ["Run `dovo history` to list valid session IDs"]
    return build_error_panel("Session Not Found", data.errors, default, fixes, raw=raw)


def _format_diff_not_found_panel(data: DiffResult | DiffResultView, *, raw: bool = False) -> Panel | str:
    """Format error panel when diff artifact is missing."""
    session_label = data.session_id or "unknown"
    default = f"Session '{session_label}' has no diff artifact."
    fixes = data.fixes or [f"Verify the session generated a diff artifact at {unresolved_diff_path(data.session_id)}"]
    return build_error_panel("Diff Not Found", data.errors, default, fixes, raw=raw)


def _format_read_failure_panel(data: DiffResult | DiffResultView, *, raw: bool = False) -> Panel | str:
    """Format error panel when diff artifact cannot be read."""
    fixes = data.fixes or ["Check file permissions and that the artifact is readable"]
    return build_error_panel("Read Failure", data.errors, "Failed to read diff artifact.", fixes, raw=raw)


def _format_diff_error_panel(data: DiffResult | DiffResultView, *, raw: bool = False) -> Panel | str:
    """Format error panel for non-ok diff results."""
    if data.status == DiffStatus.SESSION_NOT_FOUND:
        return _format_session_not_found_panel(data, raw=raw)
    if data.status == DiffStatus.DIFF_NOT_FOUND:
        return _format_diff_not_found_panel(data, raw=raw)
    if data.status == DiffStatus.READ_FAILURE:
        return _format_read_failure_panel(data, raw=raw)

    return build_error_panel("Diff Failed", data.errors, "Diff operation failed.", data.fixes, raw=raw)


class DiffResultFormatter(ComponentFormatter[DiffResult, DiffResultView]):
    """Formatter for diff command results."""

    def __init__(
        self,
        *,
        full: bool = False,
        max_lines: int | None = None,
        console: Console | None = None,
    ) -> None:
        """Initialize DiffResultFormatter with optional truncation overrides.

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

    def transform(self, data: DiffResult) -> DiffResultView:
        """Derive the presentation-ready view from DiffResult domain data.

        Args:
            data: Raw DiffResult domain model.

        Returns:
            DiffResultView containing resolved paths, line counts, and truncation flags.
        """
        relative_path = resolve_diff_rel_path(data)
        effective_max = data.max_lines if data.max_lines is not None else self.max_lines
        effective_full = data.full or self.full

        limit = effective_max if isinstance(effective_max, int) and effective_max > 0 else DEFAULT_MAX_DIFF_LINES
        diff_lines = data.diff_text.splitlines() if data.diff_text else []
        total_lines = len(diff_lines)
        is_terminal = self.console.is_terminal
        should_truncate = is_terminal and not effective_full and total_lines > limit

        return DiffResultView(
            status=data.status,
            session_id=data.session_id,
            artifact_path=data.artifact_path,
            relative_path=relative_path,
            diff_text=data.diff_text,
            raw=data.raw,
            full=effective_full,
            max_lines=effective_max,
            total_lines=total_lines,
            truncated=should_truncate,
            truncated_lines=limit if should_truncate else 0,
            errors=list(data.errors),
            warnings=list(data.warnings),
            fixes=list(data.fixes),
        )

    def to_raw(self, data: DiffResult) -> str:
        """Render a raw diff, empty message or error panel."""
        if data.status == DiffStatus.EMPTY_DIFF:
            session_label = data.session_id or "unknown"
            return f"No changes recorded for session {session_label}."

        if not data.ok:
            return str(_format_diff_error_panel(data, raw=True))

        if data.raw:
            return data.diff_text

        effective_max = data.max_lines if data.max_lines is not None else self.max_lines
        effective_full = data.full or self.full

        limit = effective_max if isinstance(effective_max, int) and effective_max > 0 else DEFAULT_MAX_DIFF_LINES
        diff_lines = data.diff_text.splitlines()
        total_lines = len(diff_lines)
        is_terminal = self.console.is_terminal
        should_truncate = is_terminal and not effective_full and total_lines > limit

        if should_truncate:
            truncated_content = "\n".join(diff_lines[:limit])
            return truncated_content

        return data.diff_text

    def to_rich(self, data: DiffResult) -> Any:
        """Render syntax-highlighted unified diff, empty message, or error panel."""
        view = self.transform(data)

        if view.status == DiffStatus.EMPTY_DIFF:
            session_label = view.session_id or "unknown"
            return Text(f"No changes recorded for session {session_label}.")

        if view.status not in (DiffStatus.OK, DiffStatus.EMPTY_DIFF) or view.errors:
            return _format_diff_error_panel(view)

        if view.raw:
            return Text(view.diff_text)

        header = Text(f"Session: {view.session_id} ({view.relative_path})\n")

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
