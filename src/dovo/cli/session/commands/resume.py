"""Root command execution logic for ``dovo session resume``."""

from __future__ import annotations

from pathlib import Path

from dovo.cli.context import CliContext
from dovo.cli.run.observer import resolve_cli_observer
from dovo.cli.run.prompter import DispatcherFailurePrompter, DispatcherInteractionPrompter
from dovo.cli.run.result import dispatch_run_result
from dovo.cli.ui import MessageEvent, ui_dispatcher
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions
from dovo.core.interaction import Interaction
from dovo.engine import BlueprintResumeService
from dovo.engine.models import BlueprintRunResult


def _emit_resume_start_notice(session_id: str | None) -> None:
    """Emit a notice that a session resume operation is starting; with no session_id, the outcome names the session."""
    if session_id:
        ui_dispatcher.dispatch(MessageEvent(message=f"Resuming session '{session_id}'..."))
        return
    ui_dispatcher.dispatch(MessageEvent(message="Resuming the latest paused session..."))


def session_resume_command(
    context: CliContext,
    session_id: str | None = None,
    *,
    no_tty: bool = False,
    env_mode: AgentEnvModeOptions | None = None,
    env_passthrough: list[str] | None = None,
    answer: str | None = None,
    comment: str | None = None,
    comment_file: Path | None = None,
    answers_file: Path | None = None,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
    display_format: DisplayFormatOptions = DisplayFormatOptions.ANSI,
) -> BlueprintRunResult:
    """Resume a paused blueprint execution session and end with its run result, after every other event."""
    ui_dispatcher.set_output_format(output_format)
    arguments = Interaction.parse_arguments(answer, comment, comment_file, answers_file)
    if not arguments.ok:
        failed = BlueprintRunResult(
            error_code=arguments.error_code,
            errors=list(arguments.errors),
            remediations=list(arguments.remediations),
            diagnostics=list(arguments.diagnostics),
        )
        dispatch_run_result(failed, non_interactive=no_tty)
        return failed

    _emit_resume_start_notice(session_id)
    interaction_prompter = DispatcherInteractionPrompter(ui_dispatcher, no_tty=no_tty)

    observer = resolve_cli_observer(
        ui_dispatcher, no_tty=no_tty, output_format=output_format, display_format=display_format
    )
    with observer:
        result = BlueprintResumeService(
            paths=context.paths,
            db=context.db.sessions,
            git=context.git,
            clock=context.clock,
            new_id=context.new_id,
            session_id=session_id,
            no_tty=no_tty,
            env_mode=None if env_mode is None else env_mode.value,
            env_passthrough=list(env_passthrough or []),
            observer=observer,
            failure_prompter=DispatcherFailurePrompter(ui_dispatcher),
            interaction_prompter=interaction_prompter,
            answers=[] if arguments.supplied is None else [arguments.supplied],
        ).execute()

    dispatch_run_result(result, non_interactive=not interaction_prompter.is_interactive)
    return result
