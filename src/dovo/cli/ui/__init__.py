"""UI domain module for formatted CLI rendering and event dispatching."""

from dovo.cli.ui.dispatcher import UiDispatcher, ui_dispatcher
from dovo.cli.ui.events import (
    ErrorPanelEvent,
    LockWaitEvent,
    LoopLifecycleEvent,
    MessageEvent,
    RunSuccessEvent,
    StepDoneEvent,
    StepOutputEvent,
    StepStartEvent,
    WarningEvent,
    WelcomeBannerEvent,
    WorktreeLifecycleEvent,
)
from dovo.cli.ui.tail import CollapsingTailDisplay

__all__ = [
    "CollapsingTailDisplay",
    "ErrorPanelEvent",
    "LockWaitEvent",
    "LoopLifecycleEvent",
    "MessageEvent",
    "RunSuccessEvent",
    "StepDoneEvent",
    "StepOutputEvent",
    "StepStartEvent",
    "UiDispatcher",
    "WarningEvent",
    "WelcomeBannerEvent",
    "WorktreeLifecycleEvent",
    "ui_dispatcher",
]
