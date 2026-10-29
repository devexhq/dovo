"""ComponentFormatter for WorkspaceInitResult."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from rich.console import Group, RenderableType
from rich.text import Text

from dovo.cli.ui.formatters.base import ComponentFormatter
from dovo.cli.ui.formatters.common import with_diagnostics
from dovo.cli.ui.formatters.init.common import (
    render_bootstrap_lines,
    render_config_lines,
    render_failure_panel,
    render_identity_lines,
    render_seed_lines,
)
from dovo.cli.ui.formatters.init.init_view import WorkspaceInitView
from dovo.common.constants import DOVO_GITIGNORE_TRACKED_ENTRIES
from dovo.common.utils import display_path
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapSummary,
    WorkspaceInitResult,
)
from dovo.core.catalog.models import SeedSummary
from dovo.core.config.models import ConfigGenerationSummary
from dovo.core.project.models import ProjectIdentityOutcome


class _ConfigPresentation(NamedTuple):
    created: bool
    overwritten: bool
    repaired: bool
    skipped_existing: bool
    config_path_relative: str | None
    inserted_keys: list[str]


def _resolve_cwd(data: WorkspaceInitResult) -> Path:
    """Resolve directory context from bootstrap root path or fallback to current directory."""
    if data.bootstrap is not None:
        return data.bootstrap.root_path.parent
    return Path.cwd()


def _extract_bootstrap_fields(
    summary: BootstrapSummary | None, cwd: Path
) -> tuple[Path | None, str | None, BootstrapOutcome | None, list[str]]:
    """Extract and format root paths, outcome, and created directories."""
    if summary is None:
        return None, None, None, []
    dirs_created = [display_path(p, cwd) for p in summary.dirs_created]
    return summary.root_path, display_path(summary.root_path, cwd), summary.outcome, dirs_created


def _extract_gitignore_fields(summary: BootstrapSummary | None, cwd: Path) -> tuple[str | None, list[str]]:
    """Extract the local .dovo/.gitignore relative path and tracked entries."""
    if summary is None:
        return None, []
    gitignore_path_relative = display_path(summary.root_path / ".gitignore", cwd)
    return gitignore_path_relative, list(DOVO_GITIGNORE_TRACKED_ENTRIES)


def _extract_identity_fields(data: WorkspaceInitResult, cwd: Path) -> tuple[str | None, str | None, bool]:
    """Extract project id, relative identity path, and whether it was preserved."""
    project_id = data.project_identity.id if data.project_identity is not None else None
    identity_path_relative = display_path(data.identity_path, cwd) if data.identity_path is not None else None
    identity_preserved = data.identity_outcome == ProjectIdentityOutcome.PRESERVED
    return project_id, identity_path_relative, identity_preserved


def _extract_config_presentation(summary: ConfigGenerationSummary | None, cwd: Path) -> _ConfigPresentation:
    """Extract configuration generation flags, relative path, and inserted keys."""
    if summary is None:
        return _ConfigPresentation(
            created=False,
            overwritten=False,
            repaired=False,
            skipped_existing=False,
            config_path_relative=None,
            inserted_keys=[],
        )
    return _ConfigPresentation(
        created=summary.created,
        overwritten=summary.overwritten,
        repaired=summary.repaired,
        skipped_existing=summary.skipped_existing,
        config_path_relative=display_path(summary.config_path, cwd),
        inserted_keys=list(summary.inserted_keys),
    )


def _extract_seed_fields(summary: SeedSummary | None, cwd: Path) -> tuple[list[str], list[str], list[str]]:
    """Extract seeded, skipped, and overwritten catalog template file paths."""
    if summary is None:
        return [], [], []
    return (
        [display_path(p, cwd) for p in summary.created_files],
        [display_path(p, cwd) for p in summary.skipped_existing_files],
        [display_path(p, cwd) for p in summary.overwritten_files],
    )


class WorkspaceInitFormatter(ComponentFormatter[WorkspaceInitResult, WorkspaceInitView]):
    """Formatter for workspace initialization results."""

    def transform(self, data: WorkspaceInitResult) -> WorkspaceInitView:
        """Derive presentation-ready view model from WorkspaceInitResult.

        Args:
            data: Domain WorkspaceInitResult instance.

        Returns:
            WorkspaceInitView with formatted relative paths and classified outcomes.
        """
        cwd = _resolve_cwd(data)

        root_path, root_path_relative, bootstrap_outcome, dirs_created = _extract_bootstrap_fields(data.bootstrap, cwd)
        gitignore_path_relative, gitignore_tracked_entries = _extract_gitignore_fields(data.bootstrap, cwd)
        project_id, identity_path_relative, identity_preserved = _extract_identity_fields(data, cwd)
        config_presentation = _extract_config_presentation(data.config_generation, cwd)
        seeded_files, skipped_seed_files, overwritten_seed_files = _extract_seed_fields(data.seed, cwd)

        return WorkspaceInitView(
            ok=data.ok,
            root_path=root_path,
            root_path_relative=root_path_relative,
            bootstrap_outcome=bootstrap_outcome,
            dirs_created=dirs_created,
            project_id=project_id,
            identity_path_relative=identity_path_relative,
            identity_preserved=identity_preserved,
            gitignore_path_relative=gitignore_path_relative,
            gitignore_tracked_entries=gitignore_tracked_entries,
            config_created=config_presentation.created,
            config_overwritten=config_presentation.overwritten,
            config_repaired=config_presentation.repaired,
            config_skipped_existing=config_presentation.skipped_existing,
            config_path_relative=config_presentation.config_path_relative,
            inserted_keys=config_presentation.inserted_keys,
            seeded_files=seeded_files,
            skipped_seed_files=skipped_seed_files,
            overwritten_seed_files=overwritten_seed_files,
            error_code=data.error_code,
            errors=list(data.errors),
            warnings=list(data.warnings),
            remediations=list(data.remediations),
            diagnostics=list(data.diagnostics),
        )

    def to_rich(self, data: WorkspaceInitResult) -> RenderableType:
        """Render the completed steps' lines, then the failure panel or the seed lines and next hint, followed by diagnostic_lines(view.diagnostics)."""
        view = self.transform(data)
        renderables: list[RenderableType] = [Text("")]
        if view.bootstrap_outcome is not None:
            renderables.extend(render_bootstrap_lines(view))
            renderables.extend(render_identity_lines(view))
            renderables.extend(render_config_lines(view))
        if not view.ok:
            renderables.append(render_failure_panel(view))
            return with_diagnostics(Group(*renderables), view.diagnostics)

        renderables.extend(render_seed_lines(view))
        renderables.append(Text(""))
        renderables.append(
            Text.from_markup(
                "[bold dim]Next: run [bold cyan]dovo config show[/bold cyan] or [bold cyan]dovo blueprint list[/bold cyan][/bold dim]"
            )
        )
        return with_diagnostics(Group(*renderables), view.diagnostics)


InitOutcomeFormatter = WorkspaceInitFormatter
