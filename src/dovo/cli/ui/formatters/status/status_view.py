from __future__ import annotations

from pathlib import Path

from dovo.cli.ui.formatters.base import ResultView
from dovo.core.status import WorkspaceHealth


class StatusView(ResultView):
    """Semantic view of a status result: no Rich markup, no composed sentences."""

    status: WorkspaceHealth
    root_dir: Path
    project_name: str | None
    config_error_code: str | None
    config_path_relative: str
    git_branch: str | None
    git_is_dirty: bool
    uncommitted_files: int
    agent_model: str | None
    active_worktrees: int | None
    max_active_worktrees: int | None
    valid_catalog_items: int | None
    total_catalog_items: int | None
    total_sessions: int
