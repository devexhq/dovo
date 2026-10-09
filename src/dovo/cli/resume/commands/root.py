"""Root command execution logic for ``dovo resume``."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.run.commands.root import _first_error
from dovo.cli.run.observer import resolve_cli_observer
from dovo.cli.run.prompter import DispatcherFailurePrompter
from dovo.cli.ui import (
    ErrorPanelEvent,
    MessageEvent,
    RunSuccessEvent,
    WarningEvent,
    ui_dispatcher,
)
from dovo.common.models import AgentEnvModeOptions, DisplayFormatOptions, OutputFormatOptions
from dovo.core.db import SessionRecord, SessionStatus
from dovo.engine import BlueprintResumeService
from dovo.engine.models import BlueprintRunResult


def _emit_resume_start_notice(context: CliContext, session_id: str | None) -> None:
    """Emit a notice that a session resume operation is starting."""
    if session_id:
        ui_dispatcher.dispatch(MessageEvent(message=f"Resuming session '{session_id}'..."))
        return
    latest = context.db.sessions.get_latest_paused()
    if latest is not None:
        ui_dispatcher.dispatch(
            MessageEvent(message=f"Resuming latest paused session '{latest.session_id}' ({latest.blueprint_name})...")
        )


def _resume_failure_msg(result: BlueprintRunResult, session_id: str | None) -> str:
    """Return the final error message for a failed resume."""
    if result.errors:
        return "\n\n".join(result.errors)
    return f"Cannot resume session '{session_id}'." if session_id else "Resume failed."


def _dispatch_resume_outcome(
    result: BlueprintRunResult,
    record: SessionRecord | None,
    session_id: str | None,
) -> None:
    """Dispatch the appropriate UI event for a completed resume operation."""
    if result.ok and record is not None:
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


def resume_command(
    context: CliContext,
    session_id: str | None = None,
    *,
    no_tty: bool = False,
    env_mode: AgentEnvModeOptions | None = None,
    env_passthrough: list[str] | None = None,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
    display_format: DisplayFormatOptions = DisplayFormatOptions.ANSI,
) -> BlueprintRunResult:
    """Resume a paused blueprint execution session."""
    ui_dispatcher.set_output_format(output_format)
    _emit_resume_start_notice(context, session_id)

    observer = resolve_cli_observer(
        ui_dispatcher, no_tty=no_tty, output_format=output_format, display_format=display_format
    )
    with observer:
        result = BlueprintResumeService(
            paths=context.paths,
            db=context.db.sessions,
            session_id=session_id,
            no_tty=no_tty,
            env_mode=None if env_mode is None else env_mode.value,
            env_passthrough=list(env_passthrough or []),
            observer=observer,
            failure_prompter=DispatcherFailurePrompter(ui_dispatcher),
        ).execute()

    for warning in result.warnings:
        ui_dispatcher.dispatch(WarningEvent(message=warning))

    record = result.session_record
    _dispatch_resume_outcome(result, record, session_id)
    return result
