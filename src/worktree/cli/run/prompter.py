"""Interactive terminal and IPC failure prompter routing via UiDispatcher."""

from __future__ import annotations

from worktree.cli.ui.dispatcher import UiDispatcher
from worktree.cli.ui.events import MessageEvent, PromptEvent, PromptOption
from worktree.core.engine import FailurePromptDecision, FailurePrompter, LoopPromptDecision
from worktree.core.step import LoopStepBlock, StepDefinition, StepResult

_STEP_CHOICE_MAP: dict[str, FailurePromptDecision] = {
    "r": FailurePromptDecision.RETRY,
    "retry": FailurePromptDecision.RETRY,
    "c": FailurePromptDecision.CONTINUE,
    "continue": FailurePromptDecision.CONTINUE,
    "a": FailurePromptDecision.ABORT,
    "abort": FailurePromptDecision.ABORT,
}

_LOOP_CHOICE_MAP: dict[str, LoopPromptDecision] = {
    "g": LoopPromptDecision.GRANT,
    "grant": LoopPromptDecision.GRANT,
    "c": LoopPromptDecision.CONTINUE,
    "continue": LoopPromptDecision.CONTINUE,
    "a": LoopPromptDecision.ABORT,
    "abort": LoopPromptDecision.ABORT,
}


class DispatcherFailurePrompter(FailurePrompter):
    """Failure prompter delegating presentation to UiDispatcher and reading from stdin when interactive."""

    def __init__(
        self,
        dispatcher: UiDispatcher,
    ) -> None:
        """Initialize prompter.

        Args:
            dispatcher: UiDispatcher instance to route prompt events through.
        """
        self._dispatcher = dispatcher

    def prompt_step_failure(
        self,
        *,
        step: StepDefinition,
        result: StepResult,
        diagnostic: str,
    ) -> FailurePromptDecision:
        """Dispatch prompt event and resolve user decision."""
        step_label = step.name or step.id
        options = [
            PromptOption(key="r", label="Retry step execution", decision="retry"),
            PromptOption(key="c", label="Continue run (ignore failure)", decision="continue"),
            PromptOption(key="a", label="Abort run", decision="abort"),
        ]
        event = PromptEvent(
            prompt_type="step_failure",
            prompt_id=step.id,
            title=f"Step '{step_label}' failed (exit code {result.exit_code}).",
            diagnostic=diagnostic or None,
            options=options,
            default="abort",
        )
        self._dispatcher.dispatch(event)

        if not (self._dispatcher.is_interactive and self._dispatcher.is_terminal_format):
            return FailurePromptDecision.ABORT

        return self._read_step_decision()

    def _read_step_decision(self) -> FailurePromptDecision:
        """Prompt user via stdin for a step failure decision (retry/continue/abort)."""
        while True:
            try:
                raw = input("Select option [r/c/a]: ")
            except EOFError:
                return FailurePromptDecision.ABORT
            choice = str(raw).strip().lower()
            decision = _STEP_CHOICE_MAP.get(choice)
            if decision is not None:
                return decision
            self._dispatcher.dispatch(MessageEvent(message="Invalid option. Enter r, c, or a (retry/continue/abort)."))

    def prompt_loop_max_iterations(
        self,
        *,
        loop: LoopStepBlock,
        iteration: int,
        diagnostic: str,
        grant_count: int = 3,
    ) -> LoopPromptDecision:
        """Dispatch loop max iterations prompt and resolve user decision."""
        options = [
            PromptOption(key="g", label=f"Grant {grant_count} additional iterations", decision="grant"),
            PromptOption(key="c", label="Continue run past loop block", decision="continue"),
            PromptOption(key="a", label="Abort run", decision="abort"),
        ]
        event = PromptEvent(
            prompt_type="loop_max_iterations",
            prompt_id=loop.id,
            title=f"\\[{loop.id}] Reached max_iterations ({loop.max_iterations}) without meeting 'until' conditions.",
            diagnostic=diagnostic or None,
            options=options,
            default="abort",
        )
        self._dispatcher.dispatch(event)

        if not (self._dispatcher.is_interactive and self._dispatcher.is_terminal_format):
            return LoopPromptDecision.ABORT

        return self._read_loop_decision()

    def _read_loop_decision(self) -> LoopPromptDecision:
        """Prompt user via stdin for a loop max-iterations decision (grant/continue/abort)."""
        while True:
            try:
                raw = input("Select option [g/c/a]: ")
            except EOFError:
                return LoopPromptDecision.ABORT
            choice = str(raw).strip().lower()
            decision = _LOOP_CHOICE_MAP.get(choice)
            if decision is not None:
                return decision
            self._dispatcher.dispatch(MessageEvent(message="Invalid option. Enter g, c, or a (grant/continue/abort)."))
