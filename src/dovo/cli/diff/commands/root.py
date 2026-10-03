"""Root command implementation for ``dovo diff`` (pure Python handler, zero Typer imports)."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.ui.dispatcher import ui_dispatcher
from dovo.core.diff import Diff, DiffResult


def diff_command(
    context: CliContext,
    session_id: str | None = None,
    *,
    raw: bool = False,
    full: bool = False,
    max_lines: int = 500,
    output_format: str = "terminal",
) -> DiffResult:
    """Execute session diff query and render results via UI dispatcher.

    Args:
        context: CLI execution context holding workspace state.
        session_id: Optional session identifier. When omitted, latest session is resolved.
        raw: When True, outputs unformatted plain text patch directly to stdout.
        full: When True, bypasses truncation in interactive terminals.
        max_lines: Optional custom line truncation threshold for testing or programmatic overrides.
        output_format: Presentation format ("terminal" or "json").

    Returns:
        Structured DiffResult with status, errors, and warnings.
    """
    result = Diff(context.paths, raw=raw, full=full, max_lines=max_lines).inspect(session_id=session_id)
    if output_format == "raw" or (raw and output_format == "terminal"):
        ui_dispatcher.dispatch(result, output_format="raw")
    else:
        ui_dispatcher.dispatch(result, output_format)
    return result
