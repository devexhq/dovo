"""Root command execution logic for ``dovo session resume``."""

from __future__ import annotations

from pathlib import Path

from dovo.cli.context import CliContext
from dovo.cli.run.commands.root import _dispatch_paused_prompt, _first_error, invalid_blueprint_panel
from dovo.cli.run.observer import resolve_cli_observer
from dovo.cli.run.prompter import DispatcherFailurePrompter, DispatcherInteractionPrompter
from dovo.cli.ui import (
    DiagnosticsEvent,
    ErrorPanelEvent,
    MessageEvent,
    RunSuccessEvent,
    WarningEvent,
    ui_dispatcher,
)
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions
from dovo.core.db import SessionRecord, SessionStatus
from dovo.core.interaction import HeadlessArgumentsResult, Interaction, InteractionAnswerStatus
from dovo.engine import BlueprintResumeService
from dovo.engine.models import BlueprintRunResult


def _emit_resume_start_notice(session_id: str | None) -> None:
    """Emit a notice that a session resume operation is starting; with no session_id, the outcome names the session."""
    if session_id:
        ui_dispatcher.dispatch(MessageEvent(message=f"Resuming session '{session_id}'..."))
        return
    ui_dispatcher.dispatch(MessageEvent(message="Resuming the latest paused session..."))


def _resume_failure_msg(result: BlueprintRunResult, session_id: str | None) -> str:
    """Return the final error message for a failed resume."""
    if result.errors:
        return "\n\n".join(result.errors)
    return f"Cannot resume session '{session_id}'." if session_id else "Resume failed."


def _headless_answers(
    answer: str | None, comment: str | None, comment_file: Path | None, answers_file: Path | None
) -> HeadlessArgumentsResult:
    """Read the comment and answers files, then combine all answer flags through Interaction.parse_arguments."""
    if comment is not None and comment_file is not None:
        return HeadlessArgumentsResult(
            status=InteractionAnswerStatus.INVALID,
            errors=["Use --comment or --comment-file together with --answer, not both."],
        )

    texts: dict[str, str | None] = {"comment": comment, "answers": None}
    for key, path in (("comment", comment_file), ("answers", answers_file)):
        if path is None:
            continue
        try:
            texts[key] = path.read_text(encoding="utf-8")
        except OSError as error:
            return HeadlessArgumentsResult(
                status=InteractionAnswerStatus.INVALID, errors=[f"Cannot read '{path}': {error}"]
            )

    return Interaction.parse_arguments(answer, texts["comment"], texts["answers"])


def _dispatch_resume_outcome(
    result: BlueprintRunResult,
    record: SessionRecord | None,
    session_id: str | None,
) -> None:
    """Dispatch the appropriate UI event for a completed resume operation."""
    subject = f"The blueprint of session '{session_id}'" if session_id else "The paused session's blueprint"
    invalid = invalid_blueprint_panel(result, subject)
    if invalid is not None:
        ui_dispatcher.dispatch(invalid)
    elif result.ok and record is not None:
        ui_dispatcher.dispatch(
            RunSuccessEvent(
                session_id=record.session_id,
                blueprint_name=record.blueprint_name,
                status=record.status,
            )
        )
    elif record is not None and record.status == SessionStatus.PAUSED:
        ui_dispatcher.dispatch(MessageEvent(message=_first_error(result, "Blueprint paused; checkpoint saved.")))
    elif record is not None and record.status == SessionStatus.CANCELLED:
        ui_dispatcher.dispatch(
            ErrorPanelEvent(title="Resume Cancelled", message=_first_error(result, "Cancelled by user."))
        )
    else:
        ui_dispatcher.dispatch(ErrorPanelEvent(title="Resume Failed", message=_resume_failure_msg(result, session_id)))


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
    """Resume a paused blueprint execution session."""
    ui_dispatcher.set_output_format(output_format)
    arguments = _headless_answers(answer, comment, comment_file, answers_file)
    if not arguments.ok:
        ui_dispatcher.dispatch(ErrorPanelEvent(title="Resume Failed", message="\n\n".join(arguments.errors)))
        return BlueprintRunResult(errors=arguments.errors)

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

    for warning in result.warnings:
        ui_dispatcher.dispatch(WarningEvent(message=warning))

    record = result.session_record
    if not _dispatch_paused_prompt(result, record, non_interactive=not interaction_prompter.is_interactive):
        _dispatch_resume_outcome(result, record, session_id)
    if output_format is OutputFormatOptions.TERMINAL and result.diagnostics:
        ui_dispatcher.dispatch(DiagnosticsEvent(diagnostics=list(result.diagnostics)))
    return result
