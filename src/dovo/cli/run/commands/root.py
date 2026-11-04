"""Root command execution logic for ``dovo run``."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.run.observer import resolve_cli_observer
from dovo.cli.run.prompter import DispatcherFailurePrompter, DispatcherInteractionPrompter
from dovo.cli.run.result import dispatch_run_result
from dovo.cli.ui import MessageEvent, ui_dispatcher
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions
from dovo.engine import BlueprintRunService
from dovo.engine.models import BlueprintRunResult


def run_command(
    context: CliContext,
    name: str,
    *,
    no_worktree: bool = False,
    keep: bool = False,
    discard: bool = False,
    agent: str | None = None,
    session_id: str | None = None,
    no_tty: bool = False,
    auto_apply: bool = False,
    cli_args: list[str] | None = None,
    env_mode: AgentEnvModeOptions | None = None,
    env_passthrough: list[str] | None = None,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
    display_format: DisplayFormatOptions = DisplayFormatOptions.ANSI,
) -> BlueprintRunResult:
    """Execute a blueprint and end with its run result, after every other event."""
    ui_dispatcher.set_output_format(output_format)
    ui_dispatcher.dispatch(MessageEvent(message=f"Running blueprint '{name}'..."))

    observer = resolve_cli_observer(
        ui_dispatcher,
        no_tty=no_tty,
        output_format=output_format,
        display_format=display_format,
    )
    interaction_prompter = DispatcherInteractionPrompter(ui_dispatcher, no_tty=no_tty)
    with observer:
        result = BlueprintRunService(
            name=name,
            paths=context.paths,
            sessions_db=context.db.sessions,
            git=context.git,
            clock=context.clock,
            new_id=context.new_id,
            no_worktree=no_worktree,
            keep=keep,
            discard=discard,
            agent=agent,
            session_id=session_id,
            cli_args=cli_args,
            no_tty=no_tty,
            auto_apply=auto_apply,
            env_mode=None if env_mode is None else env_mode.value,
            env_passthrough=list(env_passthrough or []),
            observer=observer,
            failure_prompter=DispatcherFailurePrompter(ui_dispatcher),
            interaction_prompter=interaction_prompter,
        ).execute()

    dispatch_run_result(result, non_interactive=not interaction_prompter.is_interactive)
    return result
