"""Unified UI formatters package registering all component formatters."""

from __future__ import annotations

from dovo.cli.ui.formatters.artifacts import (
    ArtifactDownloadFormatter,
    ArtifactsListFormatter,
    ArtifactsPruneFormatter,
)
from dovo.cli.ui.formatters.catalog import (
    CatalogCreateFormatter,
    CatalogDeleteFormatter,
    CatalogListFormatter,
    CatalogShowFormatter,
    CatalogValidateFormatter,
)
from dovo.cli.ui.formatters.common import (
    DispatcherProtocol,
    build_error_panel,
    render_list_errors,
    render_list_fixes,
)
from dovo.cli.ui.formatters.config import (
    ConfigLoadFormatter,
    ConfigSetFormatter,
    ConfigUnsetFormatter,
    ConfigValidateFormatter,
)
from dovo.cli.ui.formatters.diff import (
    DiffResultFormatter,
)
from dovo.cli.ui.formatters.events import (
    ErrorPanelFormatter,
    LockWaitFormatter,
    LoopLifecycleFormatter,
    MessageFormatter,
    PromptFormatter,
    RunSuccessFormatter,
    StepDoneFormatter,
    StepOutputFormatter,
    StepStartFormatter,
    WarningFormatter,
    WorktreeLifecycleFormatter,
)
from dovo.cli.ui.formatters.global_cli import (
    WelcomeBannerFormatter,
)
from dovo.cli.ui.formatters.history import (
    HistoryListFormatter,
    HistoryShowFormatter,
)
from dovo.cli.ui.formatters.init import (
    InitOutcomeFormatter,
    WorkspaceInitFormatter,
)
from dovo.cli.ui.formatters.registry import (
    FORMATTER_REGISTRY,
    register_all_formatters,
)
from dovo.cli.ui.formatters.status import (
    DovoStatusFormatter,
)
from dovo.cli.ui.formatters.worktree import (
    PrunedItemFormatter,
    WorktreeApplyFormatter,
    WorktreeCreateFormatter,
    WorktreeDeleteFormatter,
    WorktreeDiffFormatter,
    WorktreeListFormatter,
    WorktreePruneFormatter,
    WorktreeShowFormatter,
)

__all__ = [
    "FORMATTER_REGISTRY",
    "ArtifactDownloadFormatter",
    "ArtifactsListFormatter",
    "ArtifactsPruneFormatter",
    "CatalogCreateFormatter",
    "CatalogDeleteFormatter",
    "CatalogListFormatter",
    "CatalogShowFormatter",
    "CatalogValidateFormatter",
    "ConfigLoadFormatter",
    "ConfigSetFormatter",
    "ConfigUnsetFormatter",
    "ConfigValidateFormatter",
    "DiffResultFormatter",
    "DovoStatusFormatter",
    "ErrorPanelFormatter",
    "HistoryListFormatter",
    "HistoryShowFormatter",
    "InitOutcomeFormatter",
    "LockWaitFormatter",
    "LoopLifecycleFormatter",
    "MessageFormatter",
    "PromptFormatter",
    "PrunedItemFormatter",
    "RunSuccessFormatter",
    "StepDoneFormatter",
    "StepOutputFormatter",
    "StepStartFormatter",
    "WarningFormatter",
    "WelcomeBannerFormatter",
    "WorkspaceInitFormatter",
    "WorktreeApplyFormatter",
    "WorktreeCreateFormatter",
    "WorktreeDeleteFormatter",
    "WorktreeDiffFormatter",
    "WorktreeLifecycleFormatter",
    "WorktreeListFormatter",
    "WorktreePruneFormatter",
    "WorktreeShowFormatter",
    "build_error_panel",
    "register_all_formatters",
    "render_list_errors",
    "render_list_fixes",
]
