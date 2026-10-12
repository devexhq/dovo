"""Interactive terminal and IPC failure prompter routing via UiDispatcher."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from dovo.cli.ui.dispatcher import UiDispatcher
from dovo.cli.ui.events import (
    MessageEvent,
    PromptEvent,
    PromptEventArtifact,
    PromptEventComment,
    PromptOption,
)
from dovo.core.catalog.definitions import CommentPolicy, LoopStepBlock, StepDefinition
from dovo.core.interaction import COMMENT_MAX_CHARACTERS, Interaction, InteractionAnswer, InteractionRequest
from dovo.engine import FailurePromptDecision, FailurePrompter, InteractionPrompter, LoopPromptDecision
from dovo.engine.executors.models import StepResult

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


class TerminalReader:
    """Terminal line reader shared by the failure and interaction prompters."""

    def read_line(self, prompt: str) -> str:
        """Return one line read with builtin input(); EOFError and KeyboardInterrupt propagate."""
        return input(prompt)

    def read_until_eof(self) -> str:
        """Return multi-line input read until EOF (Ctrl-D)."""
        lines: list[str] = []
        while True:
            try:
                lines.append(input())
            except EOFError:
                return "\n".join(lines)

    def edit(self, editor_command: str) -> str:
        """Open editor_command on a temporary file and return its content."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comment.md"
            path.write_text("", encoding="utf-8")
            subprocess.run([*shlex.split(editor_command), str(path)], check=False)
            return path.read_text(encoding="utf-8")


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
        self._reader = TerminalReader()

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
            step_id=step.id,
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
                raw = self._reader.read_line("Select option [r/c/a]: ")
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
            step_id=loop.id,
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
                raw = self._reader.read_line("Select option [g/c/a]: ")
            except EOFError:
                return LoopPromptDecision.ABORT
            choice = str(raw).strip().lower()
            decision = _LOOP_CHOICE_MAP.get(choice)
            if decision is not None:
                return decision
            self._dispatcher.dispatch(MessageEvent(message="Invalid option. Enter g, c, or a (grant/continue/abort)."))


def paused_prompt_message(step_id: str, session_id: str, *, non_interactive: bool) -> str:
    """Return the FR-11 answer hint when non_interactive, else the FR-10 resume hint."""
    if non_interactive:
        return f"Run paused at prompt '{step_id}'. Answer with 'dovo resume {session_id} --answer {step_id}=<value>'."
    return f"Run paused at prompt '{step_id}'. Resume with 'dovo resume {session_id}'."


class DispatcherInteractionPrompter(InteractionPrompter):
    """Interaction prompter that emits a PromptEvent and reads the answer from the terminal when interactive."""

    def __init__(self, dispatcher: UiDispatcher, *, no_tty: bool = False) -> None:
        """Bind the dispatcher and whether the run was started with --no-tty."""
        self._dispatcher = dispatcher
        self._no_tty = no_tty
        self._reader = TerminalReader()

    @property
    def is_interactive(self) -> bool:
        """Return True when not --no-tty and the console is a terminal in terminal format."""
        return not self._no_tty and self._dispatcher.is_interactive and self._dispatcher.is_terminal_format

    def ask(self, request: InteractionRequest) -> InteractionAnswer | None:
        """Dispatch the prompt event, then return the answer, or None when non-interactive, interrupted or at EOF."""
        self._dispatcher.dispatch(self._event(request))
        if not self.is_interactive:
            return None

        try:
            return self._read_answer(request)
        except (EOFError, KeyboardInterrupt):
            return None

    def _event(self, request: InteractionRequest) -> PromptEvent:
        """Build the interaction PromptEvent: 1-based option keys, display labels, artifact reference, comment policies."""
        artifact = request.artifact
        return PromptEvent(
            prompt_type="interaction",
            step_id=request.step_id,
            title=request.message,
            options=[
                PromptOption(
                    key=str(position),
                    label=option.label,
                    decision=option.value,
                    comment=option.comment,
                    fail=option.fail,
                    display=f"{option.value} — {option.label}" if request.mode == "multiselect" else option.label,
                )
                for position, option in enumerate(request.options, start=1)
            ],
            default=None,
            mode=request.mode,
            message=request.message,
            artifact=None
            if artifact is None
            else PromptEventArtifact(
                name=artifact.name,
                revision=artifact.revision,
                digest=artifact.digest[:8],
                reference=f"{artifact.name}@{artifact.revision}  sha256:{artifact.digest[:8]}…",
                preview=artifact.preview,
            ),
            comment=PromptEventComment(policy=request.comment.policy, message=request.comment.message),
        )

    def _read_answer(self, request: InteractionRequest) -> InteractionAnswer:
        """Read the choice, then the comment, and return the answer only after both complete."""
        if request.mode == "text":
            return InteractionAnswer(text=self._read_text(request))

        selected = self._read_choice(request)
        policy = Interaction.comment_policy(request, selected)
        comment = None if policy == "disabled" else self._read_comment(request, policy)
        return InteractionAnswer(selected=selected, comment=comment)

    def _read_choice(self, request: InteractionRequest) -> list[str]:
        """Read and validate a 1-based index (or option value) for confirm/select, or a comma list for multiselect."""
        count = len(request.options)
        multiple = request.mode == "multiselect"
        prompt = "Select (comma-separated, Enter for none): " if multiple else f"Select [1-{count}]: "
        values = [option.value for option in request.options]
        while True:
            picked = self._pick(self._reader.read_line(prompt), values, multiple=multiple)
            if picked is not None:
                return picked
            self._dispatcher.dispatch(MessageEvent(message=f"Invalid choice. Enter a number from 1 to {count}."))

    @staticmethod
    def _pick(raw: str, values: list[str], *, multiple: bool) -> list[str] | None:
        """Return the option values named by a comma list of 1-based indexes or exact values, in option order, else None."""
        tokens = [token.strip() for token in raw.split(",") if token.strip()]
        picked = [_option_value(token, values) for token in tokens]
        if None in picked or (not multiple and len(picked) != 1):
            return None
        return [value for value in values if value in picked]

    def _read_text(self, request: InteractionRequest) -> str:
        """Read non-empty trimmed text for text mode."""
        while True:
            text = self._reader.read_line("Text (required): ").strip()
            if text:
                return text
            self._dispatcher.dispatch(MessageEvent(message=f"Prompt '{request.step_id}' requires non-empty text."))

    def _read_comment(self, request: InteractionRequest, policy: CommentPolicy) -> str | None:
        """Read the comment per policy: /edit, EOF multi-line fallback, 8000-character limit, required re-prompt."""
        suffix = "(required)" if policy == "required" else "(optional, Enter to skip)"
        prompt = f"{request.comment.message} {suffix}: "
        while True:
            line = self._reader.read_line(prompt)
            comment = (self._edit_comment() if line == "/edit" else line).rstrip()
            if len(comment) > COMMENT_MAX_CHARACTERS:
                self._dispatcher.dispatch(
                    MessageEvent(message=f"Comment is too long (max {COMMENT_MAX_CHARACTERS} characters).")
                )
                continue
            if comment or policy != "required":
                return comment or None

    def _edit_comment(self) -> str:
        """Return a comment from $VISUAL, else $EDITOR, else multi-line input read until EOF."""
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        return self._reader.edit(editor) if editor else self._reader.read_until_eof()


def _option_value(token: str, values: list[str]) -> str | None:
    """Return the option value a 1-based index or exact value token names, else None."""
    if token.isdigit() and 1 <= int(token) <= len(values):
        return values[int(token) - 1]
    return token if token in values else None
