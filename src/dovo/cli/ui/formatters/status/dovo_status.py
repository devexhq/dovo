"""ComponentFormatter for DovoStatusResult."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.status.common import build_status_table
from dovo.cli.ui.formatters.status.status_view import StatusHealth, StatusView
from dovo.common.types import ComponentFormatter
from dovo.common.utils import display_path
from dovo.core.config.loader import ConfigLoadStatus
from dovo.core.status.models import DovoStatusResult


def _derive_health(data: DovoStatusResult) -> StatusHealth:
    """Classify overall workspace health from initialization and status outcome."""
    if not data.is_initialized or data.config.status == ConfigLoadStatus.NOT_FOUND:
        return StatusHealth.UNINITIALIZED
    if not data.ok:
        return StatusHealth.DEGRADED
    return StatusHealth.OK


def _derive_project_name(data: DovoStatusResult) -> str | None:
    """Extract project name from valid config or raw dictionary fallback."""
    if data.config.config is not None and data.config.config.project.name:
        return data.config.config.project.name
    if data.config.raw is not None:
        raw_project = data.config.raw.get("project")
        if isinstance(raw_project, dict) and raw_project.get("name"):
            return str(raw_project["name"])
    return None


def _derive_worktree_counts(data: DovoStatusResult) -> tuple[int | None, int | None]:
    """Derive active and maximum worktree counts, or None when config invalid."""
    if not data.config.is_valid:
        return None, None
    return data.worktrees.active_worktrees, data.worktrees.max_active_worktrees


def _derive_catalog_counts(data: DovoStatusResult) -> tuple[int | None, int | None]:
    """Derive valid and total catalog blueprint counts, or None when config invalid."""
    if not data.config.is_valid:
        return None, None
    valid_items = data.catalog.total_items - data.catalog.invalid_items
    return valid_items, data.catalog.total_items


class DovoStatusFormatter(ComponentFormatter[DovoStatusResult, StatusView]):
    """Formatter for Dovo workspace status results."""

    def transform(self, data: DovoStatusResult) -> StatusView:
        """Derive the presentation-ready view from workspace status domain data."""
        active_worktrees, max_worktrees = _derive_worktree_counts(data)
        valid_items, total_items = _derive_catalog_counts(data)
        agent_model = (
            data.config.config.agent.model
            if (data.config.config is not None and data.config.config.agent.model)
            else None
        )
        git_branch = data.git.branch if data.git.is_git_repo else None

        return StatusView(
            health=_derive_health(data),
            root_dir=data.root_dir,
            project_name=_derive_project_name(data),
            config_status=data.config.status,
            config_path_relative=display_path(data.config.config_path, data.root_dir),
            git_branch=git_branch,
            git_is_dirty=data.git.is_dirty,
            uncommitted_files=data.git.uncommitted_files,
            agent_model=agent_model,
            active_worktrees=active_worktrees,
            max_active_worktrees=max_worktrees,
            valid_catalog_items=valid_items,
            total_catalog_items=total_items,
            total_sessions=data.database.total_sessions,
            errors=list(data.errors),
            warnings=list(data.warnings),
            remediations=list(data.fixes),
        )

    def to_rich(self, data: DovoStatusResult) -> Any:
        """Render status summary table, warnings, and remediation hints from transform(data)."""
        view = self.transform(data)
        table = build_status_table(view)
        renderables: list[Any] = [table]

        if view.warnings:
            renderables.append(Text(""))
            renderables.append(Text.from_markup("[yellow]⚠️ Configuration & Context Warnings:[/yellow]"))
            for warning in view.warnings:
                renderables.append(Text.from_markup(f"  [dim]•[/dim] {warning}"))

        if view.remediations:
            renderables.append(Text(""))
            renderables.append(Text("Next Steps & Remediation:"))
            for remediation in view.remediations:
                renderables.append(Text.from_markup(f"  [dim]•[/dim] {remediation}"))

        return Group(*renderables) if len(renderables) > 1 else renderables[0]
