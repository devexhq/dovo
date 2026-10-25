"""Agent step execution: resolved provider invocation, worktree application, and the stdout summary."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

from dovo.common.timing import Stopwatch
from dovo.core.agents import (
    AgentAttempt,
    AgentAttemptContext,
    AgentResponseStatus,
    ResolvedAgentSettings,
    allocate_invocation_paths,
    new_invocation_id,
    resolve_tool_policy,
    run_direct_attempt,
)
from dovo.core.agents.responses import diagnostics_with_remediations
from dovo.core.catalog.definitions import StepDefinition
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.engine.executors.failure_log import DiagnosticContext, logged_hint
from dovo.engine.executors.metadata import metadata_to_env
from dovo.engine.executors.models import (
    AGENT_OUTCOME_EXIT_CODES,
    AgentProgressSink,
    AgentStepRunner,
    AgentStepSummary,
    ExecutionMetadata,
    OutputCallback,
    StepDispatchOutcome,
)
from dovo.engine.executors.session_log import append_session_log_event

WORKTREE_REQUIRED_MESSAGE = (
    "Agent steps require an active git worktree.\nFix: remove --no-worktree and run the blueprint with a worktree."
)
MISSING_SETTINGS_MESSAGE = (
    "Agent step has no resolved agent settings "
    "(default_agent, agents.<provider>.model, agents.<provider>.endpoint, agents.<provider>.temperature, "
    "agents.<provider>.max_tokens)."
)
BLANK_PROMPT_MESSAGE = "Agent step prompt is blank after interpolation."
AGENT_HEARTBEAT_INTERVAL_SECONDS: Final[int] = 30


def execute_agent_step(
    step: StepDefinition,
    *,
    agent: ResolvedAgentSettings | None,
    worktree_path: Path,
    worktree_active: bool,
    session_tmp_dir: Path | None,
    main_checkout: Path,
    session_log_dir: Path | None,
    session_dir: Path | None,
    metadata: ExecutionMetadata,
    on_output: OutputCallback | None,
    on_agent_line: OutputCallback | None,
    progress: AgentProgressSink,
) -> StepDispatchOutcome:
    """Run one agent attempt through its resolved provider and return the classified dispatch outcome."""
    context: DiagnosticContext = {
        "session_id": metadata.session_id or None,
        "step_id": step.id,
        "path": str(worktree_path),
    }
    if not worktree_active:
        return _to_outcome(_provider_error(WORKTREE_REQUIRED_MESSAGE), on_output, context=context)

    if agent is None:
        return _to_outcome(_provider_error(MISSING_SETTINGS_MESSAGE), on_output, context=context)

    if not (step.prompt or "").strip():
        return _to_outcome(_provider_error(BLANK_PROMPT_MESSAGE), on_output, context=context)

    tools = resolve_tool_policy(step.tools, agent.tools)
    scratch = allocate_invocation_paths(
        invocation_id=new_invocation_id(),
        session_tmp_dir=session_tmp_dir,
        step_id=step.id,
        worktree_path=worktree_path,
        main_checkout=main_checkout,
    )
    if not scratch.ok:
        return _to_outcome(
            _provider_error(*diagnostics_with_remediations(scratch.errors, scratch.remediations)),
            on_output,
            context=context,
        )

    attempt = _run_with_heartbeat(
        step.id,
        on_output,
        progress,
        lambda: run_direct_attempt(
            instruction=step.prompt or "",
            settings=agent,
            worktree_path=worktree_path,
            timeout_seconds=step.timeout_seconds,
            tools=tools,
            max_files=step.max_files,
            context=AgentAttemptContext(
                invocation=scratch.context,
                env=step.env,
                metadata_env=metadata_to_env(metadata),
                rejected_patch_destination=_rejected_patch_destination(
                    session_dir, step.id, metadata.step.attempt, metadata.loop_iteration
                ),
                on_line=on_agent_line,
                on_progress=progress.report,
                on_env_withheld=lambda names: _report_withheld_names(names, metadata, session_log_dir, on_output),
            ),
        ),
    )
    _report_withheld_fix(attempt, agent.provider, on_output)

    return _to_outcome(attempt, on_output, context=context)


def build_agent_step_runner(
    agent: ResolvedAgentSettings | None,
    worktree_active: bool,
    *,
    session_tmp_dir: Path | None,
    main_checkout: Path,
    session_log_dir: Path | None,
    session_dir: Path | None,
) -> AgentStepRunner:
    """Return a runner closing over the run's agent settings, worktree state, scratch roots, and session log directory."""

    def _run(
        step: StepDefinition,
        worktree_path: Path,
        on_output: OutputCallback | None,
        on_agent_line: OutputCallback | None,
        progress: AgentProgressSink,
        metadata: ExecutionMetadata,
    ) -> StepDispatchOutcome:
        return execute_agent_step(
            step,
            agent=agent,
            worktree_path=worktree_path,
            worktree_active=worktree_active,
            session_tmp_dir=session_tmp_dir,
            main_checkout=main_checkout,
            session_log_dir=session_log_dir,
            session_dir=session_dir,
            metadata=metadata,
            on_output=on_output,
            on_agent_line=on_agent_line,
            progress=progress,
        )

    return _run


def _run_with_heartbeat(
    step_id: str, on_output: OutputCallback | None, progress: AgentProgressSink, run_attempt: Callable[[], AgentAttempt]
) -> AgentAttempt:
    """Run the attempt while a heartbeat thread reports elapsed whole seconds every interval it has seen no progress, and stop and join it before returning."""
    watch = Stopwatch.start()
    completed = threading.Event()

    def _beat() -> None:
        while not completed.wait(AGENT_HEARTBEAT_INTERVAL_SECONDS):
            quiet_seconds = progress.seconds_since_last_report()
            if quiet_seconds is not None and quiet_seconds < AGENT_HEARTBEAT_INTERVAL_SECONDS:
                continue

            _emit_output(on_output, "stdout", f"  agent running ({int(watch.elapsed_seconds)}s)\n")

    heartbeat = threading.Thread(target=_beat, name=f"agent-heartbeat-{step_id}", daemon=True)
    heartbeat.start()
    try:
        return run_attempt()
    finally:
        completed.set()
        heartbeat.join()


def _rejected_patch_destination(
    session_dir: Path | None, step_id: str, attempt: int, loop_iteration: int | None
) -> Path | None:
    """Return ``<session_dir>/rejected/<step_id>[_iter_<k>]_attempt_<attempt>.patch``, or None without a session directory."""
    if session_dir is None:
        return None

    iteration = f"_iter_{loop_iteration}" if loop_iteration is not None else ""

    return session_dir / "rejected" / f"{step_id}{iteration}_attempt_{attempt}.patch"


def _report_withheld_names(
    names: list[str], metadata: ExecutionMetadata, session_log_dir: Path | None, on_output: OutputCallback | None
) -> None:
    """Emit the operator stderr notice and one agent_env_filtered session-log event."""
    _emit_output(on_output, "stderr", f"  ! Agent environment filtered: {len(names)} variables withheld.\n")
    append_session_log_event(
        session_log_dir,
        SessionLogEvent(
            event=SessionLogEventType.AGENT_ENV_FILTERED,
            step_index=metadata.step.index,
            step_id=metadata.step.id,
            attempt=metadata.step.attempt,
            env_withheld=",".join(names),
        ),
    )


def _report_withheld_fix(attempt: AgentAttempt, provider: str, on_output: OutputCallback | None) -> None:
    """Emit the passthrough fix line when names were withheld and the attempt did not succeed."""
    if not attempt.env_withheld or AGENT_OUTCOME_EXIT_CODES[attempt.status] == 0:
        return

    _emit_output(
        on_output,
        "stderr",
        f"  ! Fix: add names to agents.{provider}.env_passthrough or pass --env-passthrough NAME.\n",
    )


def _provider_error(*diagnostics: str) -> AgentAttempt:
    """Build a provider_error attempt carrying the given diagnostics."""
    return AgentAttempt(status=AgentResponseStatus.PROVIDER_ERROR, diagnostics=list(diagnostics))


def _summary_line(attempt: AgentAttempt, *, status: AgentResponseStatus | None = None) -> str:
    """Serialize the attempt as exactly one compact JSON object followed by a newline."""
    summary = AgentStepSummary(
        status=status or attempt.status,
        summary=attempt.summary,
        unfixable_reason=attempt.unfixable_reason,
        touched_files=attempt.touched_files,
    )
    return summary.model_dump_json() + "\n"


def _failure_text(attempt: AgentAttempt) -> str:
    """Name the unfixable reason followed by any diagnostics, else join the diagnostics, else name the status."""
    if attempt.status == AgentResponseStatus.UNFIXABLE:
        reason_line = f"Agent reported the task unfixable: {attempt.unfixable_reason or 'no reason given'}"
        return "\n".join([reason_line, *attempt.diagnostics])

    if attempt.diagnostics:
        return "\n".join(attempt.diagnostics)

    return f"Agent step ended with status '{attempt.status.value}'."


def _emit_output(on_output: OutputCallback | None, stream_name: str, line: str) -> Exception | None:
    """Send one line on ``stream_name`` through the output callback and return the exception it raised, or None."""
    if on_output is None:
        return None

    try:
        on_output(stream_name, line)
    except Exception as exc:
        # Classified by the caller: _to_outcome records the summary-line failure; heartbeat and notices drop it.
        return exc

    return None


def _to_outcome(
    attempt: AgentAttempt, on_output: OutputCallback | None, *, context: DiagnosticContext
) -> StepDispatchOutcome:
    """Convert an attempt into its dispatch outcome carrying the attempt's warnings; a failing summary callback is recorded and downgrades success."""
    line = _summary_line(attempt)
    callback_error = _emit_output(on_output, "stdout", line)
    hint = logged_hint(callback_error, operation="engine.step", context=context) if callback_error is not None else None
    warnings = [*attempt.warnings, *([hint] if hint is not None else [])]

    if not attempt.completed:
        return _failed_outcome(
            attempt.status,
            line,
            _failure_text(attempt),
            rejected_patch_path=attempt.rejected_patch_path,
            warnings=warnings,
        )

    if callback_error is not None:
        downgraded = _summary_line(attempt, status=AgentResponseStatus.PROVIDER_ERROR)
        return _failed_outcome(
            AgentResponseStatus.PROVIDER_ERROR,
            downgraded,
            f"Agent output callback error: {callback_error}",
            warnings=warnings,
        )

    return StepDispatchOutcome(
        status="completed",
        exit_code=AGENT_OUTCOME_EXIT_CODES[attempt.status],
        stdout=line,
        stderr="",
        error_message=None,
        warnings=warnings,
    )


def _failed_outcome(
    status: AgentResponseStatus,
    line: str,
    message: str,
    *,
    rejected_patch_path: str | None = None,
    warnings: list[str],
) -> StepDispatchOutcome:
    """Build the failed dispatch outcome carrying the mapped exit code for status, the summary line, the diagnostic, any saved rejected-edit path, and the diagnostic hint warnings."""
    return StepDispatchOutcome(
        status="failed",
        exit_code=AGENT_OUTCOME_EXIT_CODES[status],
        stdout=line,
        stderr=message,
        error_message=message,
        rejected_patch_path=rejected_patch_path,
        warnings=warnings,
    )
