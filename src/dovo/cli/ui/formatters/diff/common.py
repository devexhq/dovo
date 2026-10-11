"""Shared formatting helpers for diff formatters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text

from dovo.common.utils import display_path
from dovo.core.sessions import DiffResult


def unresolved_diff_path(session_id: str | None) -> str:
    """Return the placeholder diff path shown when no artifact path was resolved."""
    return f"<sessions_dir>/{session_id or '<session_id>'}/diff.patch"


def resolve_diff_rel_path(data: DiffResult, cwd: Path | None = None) -> str:
    """Resolve display path for diff artifact relative to current working directory."""
    effective_cwd = cwd or Path.cwd()
    if data.artifact_path is not None:
        return display_path(data.artifact_path, effective_cwd)
    return unresolved_diff_path(data.session_id)


def format_truncation_notice(
    session_id: str | None,
    relative_path: str,
    limit: int,
    total_lines: int,
) -> list[Any]:
    """Render truncation notice and interaction hints."""
    session_command = f"dovo diff {session_id}" if session_id else "dovo diff"
    return [
        Text(""),
        Text(f"... [diff truncated: showing {limit} of {total_lines} lines]", style="dim"),
        Text("Hint:", style="dim"),
        Text(f"- run `{session_command} --full` to view complete formatted output", style="dim"),
        Text(f"- run `{session_command} --full | less -R` to page through formatted diff", style="dim"),
        Text(f"- run `{session_command} --raw` to output unformatted patch text", style="dim"),
        Text(f"- inspect artifact at {relative_path}", style="dim"),
    ]
