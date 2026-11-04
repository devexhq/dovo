"""UI domain module for formatted CLI rendering and event dispatching."""

from dovo.cli.ui.dispatcher import UiDispatcher, ui_dispatcher
from dovo.cli.ui.events import (
    AgentProgressEvent,
    ErrorEvent,
    IfBranchEvent,
    LockWaitEvent,
    LoopLifecycleEvent,
    MessageEvent,
    StepDoneEvent,
    StepOutputEvent,
    StepSkippedEvent,
    StepStartEvent,
    WelcomeBannerEvent,
    WorktreeLifecycleEvent,
)
from dovo.cli.ui.tail import CollapsingTailDisplay

__all__ = [
    "AgentProgressEvent",
    "CollapsingTailDisplay",
    "ErrorEvent",
    "IfBranchEvent",
    "LockWaitEvent",
    "LoopLifecycleEvent",
    "MessageEvent",
    "StepDoneEvent",
    "StepOutputEvent",
    "StepSkippedEvent",
    "StepStartEvent",
    "UiDispatcher",
    "WelcomeBannerEvent",
    "WorktreeLifecycleEvent",
    "ui_dispatcher",
]
