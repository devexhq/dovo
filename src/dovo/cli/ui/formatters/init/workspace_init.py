"""ComponentFormatter for WorkspaceInitResult."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from rich.console import Group
from rich.text import Text

from dovo.cli.ui.formatters.init.common import (
    render_bootstrap_lines,
    render_config_lines,
    render_failure_panel,
    render_identity_lines,
    render_seed_lines,
)
from dovo.cli.ui.formatters.init.init_view import WorkspaceInitView
from dovo.common.constants import DOVO_GITIGNORE_TRACKED_ENTRIES
from dovo.common.models import BaseResult
from dovo.common.types import ComponentFormatter
from dovo.common.utils import display_path
from dovo.core.bootstrap.models import (
    BootstrapOutcome,
    BootstrapResult,
    WorkspaceInitResult,
)
from dovo.core.catalog.models import SeedResult
from dovo.core.config.generator import ConfigGenerationResult
from dovo.core.project.models import ProjectIdentityProvisionResult, ProjectIdentityProvisionStatus


class _ConfigPresentation(NamedTuple):
    created: bool
    overwritten: bool
    repaired: bool
    skipped_existing: bool
    config_path_relative: str | None
    inserted_keys: list[str]


def _resolve_cwd(data: WorkspaceInitResult) -> Path:
    """Resolve directory context from bootstrap root path or fallback to current directory."""
    if data.bootstrap_result is not None and data.bootstrap_result.root_path is not None:
        return data.bootstrap_result.root_path.parent
    return Path.cwd()


def _extract_bootstrap_fields(
    result: BootstrapResult | None, cwd: Path
) -> tuple[Path | None, str | None, BootstrapOutcome | None, list[str]]:
    """Extract and format root paths, outcome, and created directories."""
    if result is None:
        return None, None, None, []
    root_path = result.root_path
    root_path_relative = display_path(root_path, cwd) if root_path is not None else None
    dirs_created = [display_path(p, cwd) for p in result.dirs_created]
    return root_path, root_path_relative, result.outcome, dirs_created


def _extract_gitignore_fields(result: BootstrapResult | None, cwd: Path) -> tuple[str | None, list[str]]:
    """Extract the local .dovo/.gitignore relative path and tracked entries."""
    if result is None:
        return None, []
    gitignore_path_relative = display_path(result.root_path / ".gitignore", cwd)
    return gitignore_path_relative, list(DOVO_GITIGNORE_TRACKED_ENTRIES)


def _extract_identity_fields(
    result: ProjectIdentityProvisionResult | None, cwd: Path
) -> tuple[str | None, str | None, bool]:
    """Extract project id, relative identity path, and whether it was preserved."""
    if result is None:
        return None, None, False
    project_id = result.identity.id if result.identity is not None else None
    identity_path_relative = display_path(result.path, cwd)
    identity_preserved = result.status == ProjectIdentityProvisionStatus.PRESERVED
    return project_id, identity_path_relative, identity_preserved


def _extract_config_presentation(result: ConfigGenerationResult | None, cwd: Path) -> _ConfigPresentation:
    """Extract configuration generation flags, relative path, and inserted keys."""
    if result is None:
        return _ConfigPresentation(
            created=False,
            overwritten=False,
            repaired=False,
            skipped_existing=False,
            config_path_relative=None,
            inserted_keys=[],
        )
    config_path_relative = display_path(result.config_path, cwd) if result.config_path is not None else None
    return _ConfigPresentation(
        created=result.created,
        overwritten=result.overwritten,
        repaired=result.repaired,
        skipped_existing=result.skipped_existing,
        config_path_relative=config_path_relative,
        inserted_keys=list(result.inserted_keys),
    )


def _extract_seed_fields(result: SeedResult | None, cwd: Path) -> tuple[list[str], list[str], list[str]]:
    """Extract seeded, skipped, and overwritten catalog template file paths."""
    if result is None:
        return [], [], []
    return (
        [display_path(p, cwd) for p in result.created_files],
        [display_path(p, cwd) for p in result.skipped_existing_files],
        [display_path(p, cwd) for p in result.overwritten_files],
    )


def _collect_messages(
    primary: list[str],
    data: WorkspaceInitResult,
    getter: Callable[[BaseResult], list[str]],
) -> list[str]:
    """Collect top-level messages or fallback to nested results."""
    messages = list(primary)
    if not messages:
        for sub_result in (data.bootstrap_result, data.identity_result, data.config_result, data.seed_result):
            if sub_result is not None:
                messages.extend(getter(sub_result))
    return messages


def _collect_errors(data: WorkspaceInitResult) -> list[str]:
    """Collect fatal errors across top-level and nested bootstrap/config/seed results."""
    return _collect_messages(data.errors, data, lambda sub_result: sub_result.errors)


def _collect_warnings(data: WorkspaceInitResult) -> list[str]:
    """Collect non-fatal warnings across top-level and nested results."""
    return _collect_messages(data.warnings, data, lambda sub_result: sub_result.warnings)


def _collect_fixes(data: WorkspaceInitResult) -> list[str]:
    """Collect remediation hints across top-level and nested results."""
    return _collect_messages(data.fixes, data, lambda sub_result: sub_result.fixes)


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

        root_path, root_path_relative, bootstrap_outcome, dirs_created = _extract_bootstrap_fields(
            data.bootstrap_result, cwd
        )
        gitignore_path_relative, gitignore_tracked_entries = _extract_gitignore_fields(data.bootstrap_result, cwd)
        project_id, identity_path_relative, identity_preserved = _extract_identity_fields(data.identity_result, cwd)
        config_presentation = _extract_config_presentation(data.config_result, cwd)
        seeded_files, skipped_seed_files, overwritten_seed_files = _extract_seed_fields(data.seed_result, cwd)

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
            failure_mode=data.failure_mode,
            errors=_collect_errors(data),
            warnings=_collect_warnings(data),
            fixes=_collect_fixes(data),
        )

    def to_rich(self, data: WorkspaceInitResult) -> Any:
        """Render initialization summary, repair details, or failure panels."""
        view = self.transform(data)
        failure_panel = render_failure_panel(view)
        if failure_panel is not None:
            return failure_panel

        renderables: list[Any] = [Text("")]
        if view.bootstrap_outcome is not None:
            renderables.extend(render_bootstrap_lines(view))
            renderables.extend(render_identity_lines(view))
            if view.config_path_relative is not None:
                renderables.extend(render_config_lines(view))
            renderables.extend(render_seed_lines(view))
        renderables.append(Text(""))
        renderables.append(
            Text.from_markup(
                "[bold dim]Next: run [bold cyan]dovo config show[/bold cyan] or [bold cyan]dovo workflow list[/bold cyan][/bold dim]"
            )
        )
        return Group(*renderables)


InitOutcomeFormatter = WorkspaceInitFormatter
