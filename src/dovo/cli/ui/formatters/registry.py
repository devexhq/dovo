"""Central registration registry and entrypoint for UI component formatters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

import dovo.cli.ui.events as event_models
import dovo.cli.ui.formatters.artifacts as artifacts_formatters
import dovo.cli.ui.formatters.catalog as catalog_formatters
import dovo.cli.ui.formatters.config as config_formatters
import dovo.cli.ui.formatters.diff as diff_formatters
import dovo.cli.ui.formatters.doctor as doctor_formatters
import dovo.cli.ui.formatters.events as event_formatters
import dovo.cli.ui.formatters.global_cli as global_formatters
import dovo.cli.ui.formatters.history as history_formatters
import dovo.cli.ui.formatters.init as init_formatters
import dovo.cli.ui.formatters.logs as logs_formatters
import dovo.cli.ui.formatters.sandbox as sandbox_formatters
import dovo.cli.ui.formatters.status as status_formatters
import dovo.core.artifacts as artifacts_models
import dovo.core.bootstrap as bootstrap_models
import dovo.core.catalog as catalog_models
import dovo.core.config as config_models
import dovo.core.diff as diff_models
import dovo.core.doctor as doctor_models
import dovo.core.history as history_models
import dovo.core.logs as logs_models
import dovo.core.sandbox as sandbox_models
import dovo.core.status as status_models
from dovo.cli.ui.formatters.common import DispatcherProtocol
from dovo.common.types import ComponentFormatter

if TYPE_CHECKING:
    from rich.console import Console

FORMATTER_REGISTRY: Final[dict[type[BaseModel], type[ComponentFormatter[Any, Any]]]] = {
    # Artifacts
    artifacts_models.ArtifactsListResult: artifacts_formatters.ArtifactsListFormatter,
    artifacts_models.ArtifactDownloadResult: artifacts_formatters.ArtifactDownloadFormatter,
    artifacts_models.ArtifactsPruneResult: artifacts_formatters.ArtifactsPruneFormatter,
    # Events
    event_models.ErrorPanelEvent: event_formatters.ErrorPanelFormatter,
    event_models.LockWaitEvent: event_formatters.LockWaitFormatter,
    event_models.WarningEvent: event_formatters.WarningFormatter,
    event_models.MessageEvent: event_formatters.MessageFormatter,
    event_models.RunSuccessEvent: event_formatters.RunSuccessFormatter,
    event_models.StepStartEvent: event_formatters.StepStartFormatter,
    event_models.StepDoneEvent: event_formatters.StepDoneFormatter,
    event_models.StepOutputEvent: event_formatters.StepOutputFormatter,
    event_models.SandboxLifecycleEvent: event_formatters.SandboxLifecycleFormatter,
    event_models.LoopLifecycleEvent: event_formatters.LoopLifecycleFormatter,
    event_models.PromptEvent: event_formatters.PromptFormatter,
    # Catalog
    catalog_models.CatalogListResult: catalog_formatters.CatalogListFormatter,
    catalog_models.CatalogShowResult: catalog_formatters.CatalogShowFormatter,
    catalog_models.CatalogDeleteResult: catalog_formatters.CatalogDeleteFormatter,
    catalog_models.CatalogCreateResult: catalog_formatters.CatalogCreateFormatter,
    catalog_models.CatalogValidateResult: catalog_formatters.CatalogValidateFormatter,
    # Config
    config_models.ConfigLoadResult: config_formatters.ConfigLoadFormatter,
    config_models.ConfigValidationResult: config_formatters.ConfigValidateFormatter,
    config_models.ConfigSetResult: config_formatters.ConfigSetFormatter,
    config_models.ConfigUnsetResult: config_formatters.ConfigUnsetFormatter,
    # Diff
    diff_models.DiffResult: diff_formatters.DiffResultFormatter,
    # Doctor
    doctor_models.DoctorReport: doctor_formatters.DoctorReportFormatter,
    # History
    history_models.HistoryListResult: history_formatters.HistoryListFormatter,
    history_models.HistoryShowResult: history_formatters.HistoryShowFormatter,
    # Logs
    logs_models.LogsShowResult: logs_formatters.LogsShowFormatter,
    # Init
    bootstrap_models.WorkspaceInitResult: init_formatters.WorkspaceInitFormatter,
    # Sandbox
    sandbox_models.PrunedItem: sandbox_formatters.PrunedItemFormatter,
    sandbox_models.SandboxPruneResult: sandbox_formatters.SandboxPruneFormatter,
    sandbox_models.SandboxShowResult: sandbox_formatters.SandboxShowFormatter,
    sandbox_models.SandboxListResult: sandbox_formatters.SandboxListFormatter,
    sandbox_models.SandboxCreateResult: sandbox_formatters.SandboxCreateFormatter,
    sandbox_models.SandboxApplyResult: sandbox_formatters.SandboxApplyFormatter,
    sandbox_models.SandboxDeleteResult: sandbox_formatters.SandboxDeleteFormatter,
    sandbox_models.SandboxDiffResult: sandbox_formatters.SandboxDiffFormatter,
    # Status
    status_models.DovoStatusResult: status_formatters.DovoStatusFormatter,
    # Global CLI
    event_models.WelcomeBannerEvent: global_formatters.WelcomeBannerFormatter,
}


def _instantiate_formatter(
    formatter_cls: type[ComponentFormatter[Any, Any]],
    console: Console | None = None,
) -> ComponentFormatter[Any, Any]:
    """Instantiate formatter, passing console if accepted by constructor.

    Args:
        formatter_cls: Concrete ComponentFormatter subclass.
        console: Optional Rich Console instance.
    """
    if formatter_cls is diff_formatters.DiffResultFormatter:
        return formatter_cls(console=console)
    return formatter_cls()


def register_all_formatters(
    dispatcher: DispatcherProtocol,
    console: Console | None = None,
) -> None:
    """Register all component formatters onto the provided dispatcher.

    Args:
        dispatcher: Target dispatcher complying with DispatcherProtocol.
        console: Optional Rich Console instance to propagate to formatters.
    """
    effective_console = console or getattr(dispatcher, "_custom_console", None)
    for model_cls, formatter_cls in FORMATTER_REGISTRY.items():
        inst = _instantiate_formatter(formatter_cls, effective_console)
        dispatcher.register(model_cls, inst)
