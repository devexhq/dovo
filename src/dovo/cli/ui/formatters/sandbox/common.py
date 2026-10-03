"""Shared tables, prompts, and formatting helpers for sandbox formatters."""

from __future__ import annotations

from rich.table import Table

from dovo.core.db import SandboxRecord
from dovo.core.sandbox.models import (
    StaleSandboxCategory,
)

_SANDBOX_SHOW_FIELDS = (
    "ID",
    "Name",
    "Branch",
    "Base Commit",
    "Path",
    "Status",
    "Disk",
    "Created",
    "Updated",
)

CATEGORY_LABELS: dict[StaleSandboxCategory, str] = {
    StaleSandboxCategory.STALE_BRANCH: "stale branch",
    StaleSandboxCategory.ORPHANED_DIRECTORY: "orphaned directory",
    StaleSandboxCategory.STALE_WORKTREE_REF: "stale worktree ref",
    StaleSandboxCategory.STALE_DB_RECORD: "stale db record",
}


def build_sandbox_table(sandboxes: list[SandboxRecord]) -> Table:
    """Build the Dovo Sandboxes table for list output."""
    table = Table(title="Dovo Sandboxes", title_justify="left", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Status")
    table.add_column("Created")

    for row in sandboxes:
        name = row.name if row.name is not None else "[dim]-[/dim]"
        table.add_row(
            row.id,
            name,
            row.branch_name,
            row.status.value,
            row.created_at,
        )
    return table


def build_sandbox_detail_table(sandbox: SandboxRecord, *, disk_present: bool) -> Table:
    """Build the key/value detail table for sandbox show."""
    name = sandbox.name if sandbox.name is not None else "-"
    disk = "present" if disk_present else "missing"
    values = {
        "ID": sandbox.id,
        "Name": name,
        "Branch": sandbox.branch_name,
        "Base Commit": sandbox.base_commit,
        "Path": str(sandbox.sandbox_path),
        "Status": sandbox.status.value,
        "Disk": disk,
        "Created": sandbox.created_at,
        "Updated": sandbox.updated_at,
    }

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="bold")
    table.add_column()
    for field in _SANDBOX_SHOW_FIELDS:
        table.add_row(f"{field}:", values[field])
    return table
