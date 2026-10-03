"""Root command execution logic for ``dovo run``."""

from __future__ import annotations

from dovo.cli.context import CliContext
from dovo.cli.run.observer import resolve_cli_observer
from dovo.cli.run.prompter import DispatcherFailurePrompter
from dovo.cli.ui import (
    ErrorPanelEvent,
    MessageEvent,
    RunSuccessEvent,
    WarningEvent,
    ui_dispatcher,
)
from dovo.common.models import DisplayFormatOptions, OutputFormatOptions
from dovo.core.db import RunRecord, RunStatus
from dovo.engine import BlueprintRunService
from dovo.engine.models import BlueprintRunResult


def _first_error(result: BlueprintRunResult, fallback: str) -> str:
    """Return the first error message or a fallback string."""
    return result.errors[0] if result.errors else fallback


def _dispatch_run_outcome(
    result: BlueprintRunResult,
    record: RunRecord | None,
) -> None:
    """Dispatch the appropriate UI event for a completed blueprint run."""
    if result.ok and record is not None:
        ui_dispatcher.dispatch(
            RunSuccessEvent(
                session_id=record.session_id,
                blueprint_name=record.blueprint_name,
                status=record.status,
            )
        )
    elif record is not None and record.status == RunStatus.PAUSED:
        ui_dispatcher.dispatch(MessageEvent(message=_first_error(result, "Blueprint paused; checkpoint saved.")))
    elif record is not None and record.status == RunStatus.CANCELLED:
        ui_dispatcher.dispatch(
            ErrorPanelEvent(
                title="Blueprint Run Cancelled",
                message=_first_error(result, "Cancelled by user."),
            )
        )
    else:
        msg = "\n\n".join(result.errors) if result.errors else "Blueprint execution failed."
        ui_dispatcher.dispatch(ErrorPanelEvent(title="Run Failed", message=msg))


def run_command(
    context: CliContext,
    name: str,
    *,
    no_sandbox: bool = False,
    keep: bool = False,
    agent: str | None = None,
    session_id: str | None = None,
    no_tty: bool = False,
    auto_apply: bool = False,
    cli_args: list[str] | None = None,
    output_format: OutputFormatOptions = OutputFormatOptions.TERMINAL,
    display_format: DisplayFormatOptions = DisplayFormatOptions.ANSI,
) -> BlueprintRunResult:
    """Execute a blueprint."""
    ui_dispatcher.set_output_format(output_format)
    ui_dispatcher.dispatch(MessageEvent(message=f"Running blueprint '{name}'..."))

    observer = resolve_cli_observer(
        ui_dispatcher,
        no_tty=no_tty,
        output_format=output_format,
        display_format=display_format,
    )
    with observer:
        result = BlueprintRunService(
            name=name,
            paths=context.paths,
            runs_db=context.db.runs,
            no_sandbox=no_sandbox,
            keep=keep,
            agent=agent,
            session_id=session_id,
            cli_args=cli_args,
            no_tty=no_tty,
            auto_apply=auto_apply,
            observer=observer,
            failure_prompter=DispatcherFailurePrompter(ui_dispatcher),
        ).execute()

    for warning in result.warnings:
        ui_dispatcher.dispatch(WarningEvent(message=warning))

    record = result.run_record
    _dispatch_run_outcome(result, record)
    return result
