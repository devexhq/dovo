"""Central registration registry and entrypoint for UI component formatters.

Formatters are grouped by the package that defines the models they render, and a group is imported only when one of
its models is first dispatched, so a command imports the formatters it renders and not every other command's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

from dovo.cli.ui.formatters.base import ComponentFormatter

if TYPE_CHECKING:
    from rich.console import Console

type FormatterTable = dict[type[BaseModel], ComponentFormatter[Any]]


def _artifacts_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.artifacts` results."""
    import dovo.cli.ui.formatters.artifacts as formatters
    import dovo.core.artifacts as models

    return {
        models.ArtifactsListResult: formatters.ArtifactsListFormatter(),
        models.ArtifactDownloadResult: formatters.ArtifactDownloadFormatter(),
        models.ArtifactsPruneResult: formatters.ArtifactsPruneFormatter(),
    }


def _event_formatters(console: Console | None) -> FormatterTable:
    """Formatters for the runtime UI events in `dovo.cli.ui.events`."""
    import dovo.cli.ui.events as models
    import dovo.cli.ui.formatters.events as formatters
    import dovo.cli.ui.formatters.global_cli as global_formatters

    return {
        models.ErrorPanelEvent: formatters.ErrorPanelFormatter(),
        models.LockWaitEvent: formatters.LockWaitFormatter(),
        models.WarningEvent: formatters.WarningFormatter(),
        models.DiagnosticsEvent: formatters.DiagnosticsFormatter(),
        models.MessageEvent: formatters.MessageFormatter(),
        models.RunSuccessEvent: formatters.RunSuccessFormatter(),
        models.StepStartEvent: formatters.StepStartFormatter(),
        models.StepDoneEvent: formatters.StepDoneFormatter(),
        models.StepSkippedEvent: formatters.StepSkippedFormatter(),
        models.IfBranchEvent: formatters.IfBranchFormatter(),
        models.StepOutputEvent: formatters.StepOutputFormatter(),
        models.AgentProgressEvent: formatters.AgentProgressFormatter(),
        models.WorktreeLifecycleEvent: formatters.WorktreeLifecycleFormatter(),
        models.LoopLifecycleEvent: formatters.LoopLifecycleFormatter(),
        models.PromptEvent: formatters.PromptFormatter(),
        models.WelcomeBannerEvent: global_formatters.WelcomeBannerFormatter(),
    }


def _catalog_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.catalog` results."""
    import dovo.cli.ui.formatters.catalog as formatters
    import dovo.core.catalog as models

    return {
        models.CatalogListResult: formatters.CatalogListFormatter(),
        models.CatalogShowResult: formatters.CatalogShowFormatter(),
        models.CatalogDeleteResult: formatters.CatalogDeleteFormatter(),
        models.CatalogCreateResult: formatters.CatalogCreateFormatter(),
        models.CatalogValidateResult: formatters.CatalogValidateFormatter(),
    }


def _config_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.config` results."""
    import dovo.cli.ui.formatters.config as formatters
    import dovo.core.config as models

    return {
        models.ConfigLoadResult: formatters.ConfigLoadFormatter(),
        models.ConfigValidationResult: formatters.ConfigValidateFormatter(),
        models.ConfigSetResult: formatters.ConfigSetFormatter(),
        models.ConfigUnsetResult: formatters.ConfigUnsetFormatter(),
    }


def _diagnostics_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.diagnostics` results."""
    import dovo.cli.ui.formatters.doctor as formatters
    import dovo.core.diagnostics as models

    return {models.DiagnosticsReport: formatters.DoctorReportFormatter()}


def _sessions_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.sessions` results; the diff formatter renders through the dispatcher's console."""
    import dovo.cli.ui.formatters.session as formatters
    import dovo.core.sessions as models

    return {
        models.DiffResult: formatters.SessionDiffFormatter(console=console),
        models.HistoryListResult: formatters.SessionListFormatter(),
        models.HistoryShowResult: formatters.SessionShowFormatter(),
        models.LogsShowResult: formatters.SessionLogsFormatter(),
    }


def _bootstrap_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.bootstrap` results."""
    import dovo.cli.ui.formatters.init as formatters
    import dovo.core.bootstrap as models

    return {models.WorkspaceInitResult: formatters.WorkspaceInitFormatter()}


def _worktree_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.worktree` results."""
    import dovo.cli.ui.formatters.worktree as formatters
    import dovo.core.worktree as models

    return {
        models.PrunedItem: formatters.PrunedItemFormatter(),
        models.WorktreePruneResult: formatters.WorktreePruneFormatter(),
        models.WorktreeShowResult: formatters.WorktreeShowFormatter(),
        models.WorktreeListResult: formatters.WorktreeListFormatter(),
        models.WorktreeCreateResult: formatters.WorktreeCreateFormatter(),
        models.WorktreeApplyResult: formatters.WorktreeApplyFormatter(),
        models.WorktreeDeleteResult: formatters.WorktreeDeleteFormatter(),
        models.WorktreeDiffResult: formatters.WorktreeDiffFormatter(),
    }


def _status_formatters(console: Console | None) -> FormatterTable:
    """Formatters for `dovo.core.status` results."""
    import dovo.cli.ui.formatters.status as formatters
    import dovo.core.status as models

    return {models.DovoStatusResult: formatters.DovoStatusFormatter()}


# Each group's formatters, keyed by the package whose modules define the models they render.
FORMATTER_GROUPS: Final[dict[str, Callable[[Console | None], FormatterTable]]] = {
    "dovo.core.artifacts": _artifacts_formatters,
    "dovo.cli.ui.events": _event_formatters,
    "dovo.core.catalog": _catalog_formatters,
    "dovo.core.config": _config_formatters,
    "dovo.core.diagnostics": _diagnostics_formatters,
    "dovo.core.sessions": _sessions_formatters,
    "dovo.core.bootstrap": _bootstrap_formatters,
    "dovo.core.worktree": _worktree_formatters,
    "dovo.core.status": _status_formatters,
}


def formatters_for(model_cls: type[object], console: Console | None = None) -> FormatterTable:
    """Return every formatter in the group that renders `model_cls`, importing that group.

    Args:
        model_cls: Model type about to be dispatched; a type outside every group yields an empty table.
        console: Optional Rich Console instance to propagate to formatters.
    """
    for package, group in FORMATTER_GROUPS.items():
        if model_cls.__module__ == package or model_cls.__module__.startswith(f"{package}."):
            return group(console)
    return {}
