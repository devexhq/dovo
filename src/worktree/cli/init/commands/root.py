"""Handles local workspace initialization (`wt init`)."""

from __future__ import annotations

from worktree.cli.context import CliContext
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.core.bootstrap import WorkspaceInitResult, initialize_workspace


def init_command(
    context: CliContext,
    tool_version: str | None = None,
    overwrite: bool = False,
    repair: bool = False,
    output_format: str = "terminal",
    project_id: str | None = None,
    display_name: str | None = None,
    force: bool = False,
) -> WorkspaceInitResult:
    """Initialize a local project workspace for Worktree CLI and desktop sync.

    Args:
        context: CLI context instance.
        tool_version: Optional version stamp for bootstrap metadata.
        overwrite: When True, replace existing config with V1 defaults.
        repair: When True, non-destructively add missing required keys.
        output_format: Presentation format ("terminal" or "json").
        project_id: Explicit unique project ID slug.
        display_name: Human-readable project display name.
        force: When True with project_id, overwrite an existing project identity.
    """
    result = initialize_workspace(
        context.paths.root_dir,
        tool_version=tool_version,
        overwrite=overwrite,
        repair=repair,
        project_id=project_id,
        display_name=display_name,
        force=force,
    )
    ui_dispatcher.dispatch(result, output_format=output_format)
    return result
