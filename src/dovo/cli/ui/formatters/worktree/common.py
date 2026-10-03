"""Shared tables, prompts, and formatting helpers for worktree formatters."""

from __future__ import annotations

from rich.table import Table

from dovo.core.db import WorktreeRecord
from dovo.core.worktree.models import (
    StaleWorktreeCategory,
)

_WORKTREE_SHOW_FIELDS = (
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

CATEGORY_LABELS: dict[StaleWorktreeCategory, str] = {
    StaleWorktreeCategory.STALE_BRANCH: "stale branch",
    StaleWorktreeCategory.ORPHANED_DIRECTORY: "orphaned directory",
    StaleWorktreeCategory.STALE_WORKTREE_REF: "stale worktree ref",
    StaleWorktreeCategory.STALE_DB_RECORD: "stale db record",
}


def build_worktree_table(worktrees: list[WorktreeRecord]) -> Table:
    """Build the Dovo Worktrees table for list output."""
    table = Table(title="Dovo Worktrees", title_justify="left", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Status")
    table.add_column("Created")

    for row in worktrees:
        name = row.name if row.name is not None else "[dim]-[/dim]"
        table.add_row(
            row.id,
            name,
            row.branch_name,
            row.status.value,
            row.created_at,
        )
    return table


def build_worktree_detail_table(worktree: WorktreeRecord, *, disk_present: bool) -> Table:
    """Build the key/value detail table for worktree show."""
    name = worktree.name if worktree.name is not None else "-"
    disk = "present" if disk_present else "missing"
    values = {
        "ID": worktree.id,
        "Name": name,
        "Branch": worktree.branch_name,
        "Base Commit": worktree.base_commit,
        "Path": str(worktree.worktree_path),
        "Status": worktree.status.value,
        "Disk": disk,
        "Created": worktree.created_at,
        "Updated": worktree.updated_at,
    }

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="bold")
    table.add_column()
    for field in _WORKTREE_SHOW_FIELDS:
        table.add_row(f"{field}:", values[field])
    return table
