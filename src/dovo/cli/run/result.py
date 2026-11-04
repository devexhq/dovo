"""The run result that ends every ``dovo run`` and ``dovo resume``."""

from __future__ import annotations

from dovo.cli.run.prompter import paused_prompt_message
from dovo.cli.ui import MessageEvent, ui_dispatcher
from dovo.engine.models import BlueprintRunResult


def dispatch_run_result(result: BlueprintRunResult, *, non_interactive: bool) -> None:
    """Dispatch the paused-prompt hint when the run paused at an ask, then the run result as the invocation's last event."""
    if result.paused_prompt_id is not None and result.session_id is not None:
        message = paused_prompt_message(result.paused_prompt_id, result.session_id, non_interactive=non_interactive)
        ui_dispatcher.dispatch(MessageEvent(message=message))
    ui_dispatcher.dispatch(result)
