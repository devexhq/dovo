"""Shared tables and formatting helpers for artifacts formatters."""

from __future__ import annotations

from typing import Final

from rich.table import Table

from dovo.cli.ui.formatters.artifacts.artifacts_views import ArtifactRowView

_SIZE_UNITS: Final[tuple[str, ...]] = ("B", "KB", "MB", "GB", "TB")


def format_size_bytes(size_bytes: int) -> str:
    """Format a byte count as a human-readable string (e.g. '1.02 MB')."""
    value = float(size_bytes)
    for unit in _SIZE_UNITS:
        if value < 1024 or unit == _SIZE_UNITS[-1]:
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} {_SIZE_UNITS[-1]}"


def build_artifacts_table(artifacts: list[ArtifactRowView]) -> Table:
    """Build the Dovo Artifacts table for list output."""
    table = Table(title="Dovo Artifacts", title_justify="left", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Session")
    table.add_column("Files")
    table.add_column("Size")
    table.add_column("Expires")

    for row in artifacts:
        table.add_row(
            row.name,
            row.session_id,
            str(row.file_count),
            row.size_display,
            row.expires_at or "[dim]never[/dim]",
        )
    return table
