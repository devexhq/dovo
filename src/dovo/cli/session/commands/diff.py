"""Root command implementation for ``dovo session diff`` (pure Python handler, zero Typer imports)."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.common.models import OutputFormatOptions
from dovo.core.sessions import DiffResult, SessionCollection


def session_diff_command(
    context: CliContext,
    session_id: str | None = None,
    *,
    patch: bool = False,
    full: bool = False,
    max_lines: int = 500,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
) -> DiffResult:
    """Execute session diff query and render results via UI dispatcher.

    Args:
        context: CLI execution context holding workspace state.
        session_id: Optional session identifier. When omitted, latest session is resolved.
        patch: When True and the diff was read, writes the patch text verbatim to stdout instead of rendering it.
        full: When True, bypasses truncation in interactive terminals.
        max_lines: Optional custom line truncation threshold for testing or programmatic overrides.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        Structured DiffResult with status, errors, and warnings.
    """
    sessions = SessionCollection(context.paths, db=context.db.sessions)
    result = sessions.latest_diff() if session_id is None else sessions.get(session_id).diff()
    if result.ok:
        result.full = full
        result.max_lines = max_lines
        result.latest = session_id is None

    if patch and result.ok:
        ui_dispatcher.write_text(result.diff_text)
    else:
        ui_dispatcher.dispatch(result, output_format=output_format)
    return result
