"""Unified UI formatters package registering all component formatters."""

from __future__ import annotations

from worktree.cli.ui.formatters.artifacts import (
    ArtifactDownloadFormatter,
    ArtifactsListFormatter,
    ArtifactsPruneFormatter,
)
from worktree.cli.ui.formatters.catalog import (
    CatalogCreateFormatter,
    CatalogDeleteFormatter,
    CatalogListFormatter,
    CatalogShowFormatter,
    CatalogValidateFormatter,
)
from worktree.cli.ui.formatters.common import (
    DispatcherProtocol,
    build_error_panel,
    render_list_errors,
    render_list_fixes,
)
from worktree.cli.ui.formatters.config import (
    ConfigLoadFormatter,
    ConfigSetFormatter,
    ConfigUnsetFormatter,
    ConfigValidateFormatter,
)
from worktree.cli.ui.formatters.diff import (
    DiffResultFormatter,
)
from worktree.cli.ui.formatters.events import (
    ErrorPanelFormatter,
    LockWaitFormatter,
    LoopLifecycleFormatter,
    MessageFormatter,
    PromptFormatter,
    RunSuccessFormatter,
    SandboxLifecycleFormatter,
    StepDoneFormatter,
    StepOutputFormatter,
    StepStartFormatter,
    WarningFormatter,
)
from worktree.cli.ui.formatters.global_cli import (
    WelcomeBannerFormatter,
)
from worktree.cli.ui.formatters.history import (
    HistoryListFormatter,
    HistoryShowFormatter,
)
from worktree.cli.ui.formatters.init import (
    InitOutcomeFormatter,
    WorkspaceInitFormatter,
)
from worktree.cli.ui.formatters.registry import (
    FORMATTER_REGISTRY,
    register_all_formatters,
)
from worktree.cli.ui.formatters.sandbox import (
    PrunedItemFormatter,
    SandboxApplyFormatter,
    SandboxCreateFormatter,
    SandboxDeleteFormatter,
    SandboxDiffFormatter,
    SandboxListFormatter,
    SandboxPruneFormatter,
    SandboxShowFormatter,
)
from worktree.cli.ui.formatters.status import (
    WorktreeStatusFormatter,
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
    "SandboxApplyFormatter",
    "SandboxCreateFormatter",
    "SandboxDeleteFormatter",
    "SandboxDiffFormatter",
    "SandboxLifecycleFormatter",
    "SandboxListFormatter",
    "SandboxPruneFormatter",
    "SandboxShowFormatter",
    "StepDoneFormatter",
    "StepOutputFormatter",
    "StepStartFormatter",
    "WarningFormatter",
    "WelcomeBannerFormatter",
    "WorkspaceInitFormatter",
    "WorktreeStatusFormatter",
    "build_error_panel",
    "register_all_formatters",
    "render_list_errors",
    "render_list_fixes",
]
