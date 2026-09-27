"""Shared multi-step execution engine with optional sandbox lifecycle."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from worktree.common.models import FailurePolicy
from worktree.common.process import process_registry
from worktree.core.config import ConfigLoadError
from worktree.core.db import RunStatus, SandboxesRepository
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.diff.writer import get_session_dir, write_session_diff
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_project_filesystem_paths
from worktree.core.runtime.artifact_publish import auto_publish_step_artifacts
from worktree.core.runtime.exceptions import PromptUserInterruptedError
from worktree.core.runtime.failure import (
    effective_terminal_policy,
    mark_continued_after_prompt,
    step_failure_diagnostic,
)
from worktree.core.runtime.log_writer import append_run_log_event
from worktree.core.runtime.loop_runner import LoopBlockRunner
from worktree.core.runtime.models import (
    FailurePromptDecision,
    RunCheckpoint,
    RunContext,
    RunLogEvent,
    RunLogEventType,
    RunOutcome,
    StepLoopState,
)
from worktree.core.sandbox import (
    Sandbox,
    SandboxApplyStrategy,
    SandboxSession,
)
from worktree.core.step import (
    LoopStepBlock,
    PreviousStepMetadata,
    StepDefinition,
    StepExecution,
    StepExecutionContext,
    StepResult,
)
from worktree.core.step.services.metadata import previous_step_metadata_from_result


def _notify_sandbox_ready(context: RunContext, path: Path, *, active: bool) -> None:
    """Notify observer that sandbox environment is prepared."""
    if context.observer is not None:
        try:
            context.observer.on_sandbox_ready(path, active)
        except Exception:
            pass


def _notify_step_start(context: RunContext, idx: int, total: int, step: StepDefinition) -> None:
    """Notify observer that step execution is beginning."""
    if context.observer is not None:
        try:
            context.observer.on_step_start(idx, total, step)
        except Exception:
            pass


def _notify_step_output(
    context: RunContext,
    idx: int,
    total: int,
    step: StepDefinition,
    line: str,
    *,
    stream: str = "stdout",
) -> None:
    """Notify observer of incremental step stdout or stderr output line."""
    if context.observer is not None:
        try:
            context.observer.on_step_output(idx, total, step, line, stream=stream)
        except Exception:
            pass


def _notify_step_done(context: RunContext, idx: int, total: int, result: StepResult) -> None:
    """Notify observer that step execution has completed."""
    if context.observer is not None:
        try:
            context.observer.on_step_done(idx, total, result)
        except Exception:
            pass


def _notify_sandbox_cleanup(context: RunContext, *, kept: bool, path: Path) -> None:
    """Notify observer of sandbox cleanup outcome."""
    if context.observer is not None:
        try:
            context.observer.on_sandbox_cleanup(kept, path)
        except Exception:
            pass


def _session_from_checkpoint(checkpoint: RunCheckpoint, path: Path) -> SandboxSession:
    """Reconstruct SandboxSession from saved checkpoint fields."""
    return SandboxSession(
        session_id=checkpoint.sandbox_id or "resumed",
        target_branch=checkpoint.sandbox_branch or "worktree/sandbox-resumed",
        sandbox_path=path,
        base_commit=checkpoint.sandbox_base_commit or "HEAD",
        name=checkpoint.sandbox_name,
        created_at="",
    )


def _setup_resumed_sandbox(
    context: RunContext,
    checkpoint: RunCheckpoint,
) -> tuple[Path, Sandbox | None, SandboxSession | None, str | None]:
    """Validate and prepare resumed sandbox session from checkpoint."""
    if not checkpoint.use_sandbox:
        target_dir = context.cwd.resolve()
        _notify_sandbox_ready(context, target_dir, active=False)
        return target_dir, None, None, None

    path = Path(checkpoint.sandbox_path or "")
    if not path.exists():
        return context.cwd.resolve(), None, None, f"Git sandbox is missing: {path}"

    session = _session_from_checkpoint(checkpoint, path)
    manager = Sandbox(context.cwd.resolve(), db=SandboxesRepository(context.cwd.resolve()))
    _notify_sandbox_ready(context, path, active=True)
    return path, manager, session, None


def _setup_sandbox(
    context: RunContext,
) -> tuple[Path, Sandbox | None, SandboxSession | None, str | None]:
    """Create an optional sandbox and return the execution directory.

    Returns:
        Tuple of (target_dir, manager, session, error_message).
        ``error_message`` is set when sandbox setup fails.
    """
    if context.resume_from is not None:
        return _setup_resumed_sandbox(context, context.resume_from)

    if not context.use_sandbox:
        target_dir = context.cwd.resolve()
        _notify_sandbox_ready(context, target_dir, active=False)
        return target_dir, None, None, None

    manager = Sandbox(context.cwd.resolve(), db=SandboxesRepository(context.cwd.resolve()))
    session_id = None
    if context.identity is not None:
        session_id = context.identity.blueprint_key or None
    try:
        create_result = manager.create(session_id=session_id)
    except ConfigLoadError as exc:
        return context.cwd.resolve(), None, None, f"Git sandbox creation failed: {exc}"
    if not create_result.ok or create_result.session is None:
        detail = create_result.errors[0] if create_result.errors else "Sandbox creation failed."
        return context.cwd.resolve(), None, None, f"Git sandbox creation failed: {detail}"

    session = create_result.session
    target_dir = session.sandbox_path
    _notify_sandbox_ready(context, target_dir, active=True)
    return target_dir, manager, session, None


def _cleanup_sandbox(
    context: RunContext,
    manager: Sandbox | None,
    session: SandboxSession | None,
    target_dir: Path,
) -> bool:
    """Clean up sandbox unless keep is requested. Returns whether it was kept."""
    if manager is None or session is None:
        _notify_sandbox_cleanup(context, kept=False, path=target_dir)
        return False

    if context.keep:
        _notify_sandbox_cleanup(context, kept=True, path=session.sandbox_path)
        return True

    try:
        manager.cleanup(session)
    except Exception:
        # Best-effort cleanup: worktree removal is independent of run outcome.
        pass
    _notify_sandbox_cleanup(context, kept=False, path=session.sandbox_path)
    return False


def _failed_step_message(result: StepResult) -> str:
    """Format diagnostic message describing step failure."""
    detail = step_failure_diagnostic(result)
    return f"Step '{result.step_id}' failed: {detail}"


def _build_step_context(context: RunContext) -> dict[str, object] | None:
    """Build the per-step execution context, including resolved inputs."""
    step_context: dict[str, object] = {}
    if context.agent:
        step_context["agent"] = context.agent
    if context.inputs:
        step_context["inputs"] = context.inputs
    return step_context or None


def _build_checkpoint(
    context: RunContext,
    state: StepLoopState,
    *,
    step: StepDefinition,
    result: StepResult,
    step_index: int,
) -> RunCheckpoint:
    """Construct RunCheckpoint snapshot for pausing execution."""
    diagnostic = _failed_step_message(result)
    session = state.session
    return RunCheckpoint(
        next_step_index=step_index,
        step_results=list(state.step_results),
        sandbox_path=str(session.sandbox_path if session is not None else state.target_dir),
        sandbox_id=session.session_id if session is not None else None,
        sandbox_name=session.name if session is not None else None,
        sandbox_branch=session.target_branch if session is not None else None,
        sandbox_base_commit=session.base_commit if session is not None else None,
        use_sandbox=context.use_sandbox,
        keep=context.keep,
        agent=context.agent,
        inputs=dict(context.inputs or {}),
        identity=context.identity,
        pending_step_id=step.id,
        diagnostic=diagnostic,
        pending_result=result,
    )


def _try_save_checkpoint(context: RunContext, checkpoint: RunCheckpoint, warnings: list[str]) -> bool:
    """Attempt to save execution checkpoint to pause store, recording any warnings."""
    if context.pause_store is None:
        return False
    try:
        context.pause_store.save_checkpoint(checkpoint)
    except Exception as exc:
        warnings.append(f"Failed to persist pause checkpoint: {exc}")
        return False
    return True


def _try_clear_pause(context: RunContext, warnings: list[str]) -> None:
    """Attempt to clear persisted pause state, recording any warnings."""
    if context.pause_store is None:
        return
    try:
        context.pause_store.clear_pause()
    except Exception as exc:
        warnings.append(f"Failed to clear pause checkpoint: {exc}")


def _prompt_user_decision(
    context: RunContext,
    state: StepLoopState,
    step: StepDefinition,
    result: StepResult,
    step_index: int,
) -> tuple[FailurePromptDecision, str | None]:
    """Resolve a ``prompt_user`` decision, degrading to abort when non-interactive."""
    diagnostic = step_failure_diagnostic(result)
    if context.no_tty or context.failure_prompter is None:
        if context.no_tty:
            warning = f"Warning: step '{step.id}' requested prompt_user but the run is non-interactive; aborting."
        else:
            warning = (
                f"Warning: step '{step.id}' requested prompt_user but no failure prompter is configured; aborting."
            )
        return FailurePromptDecision.ABORT, warning

    checkpoint = _build_checkpoint(context, state, step=step, result=result, step_index=step_index)
    persisted = _try_save_checkpoint(context, checkpoint, state.warnings)
    try:
        decision = context.failure_prompter.prompt_step_failure(
            step=step,
            result=result,
            diagnostic=diagnostic,
        )
    except KeyboardInterrupt:
        if persisted:
            raise PromptUserInterruptedError(checkpoint.diagnostic) from None
        raise
    _try_clear_pause(context, state.warnings)
    return decision, None


def _apply_prompt_decision(
    decision: FailurePromptDecision,
    result: StepResult,
) -> tuple[str, StepResult | None, str | None]:
    """Map a prompt decision to orchestration action.

    Returns:
        ``(action, result_to_record, error_message)`` where action is one of
        ``retry``, ``continue``, ``abort``.
    """
    if decision == FailurePromptDecision.RETRY:
        return "retry", None, None
    if decision == FailurePromptDecision.CONTINUE:
        return "continue", mark_continued_after_prompt(result), None
    return "abort", result, _failed_step_message(result)


def _handle_failed_step(
    context: RunContext,
    state: StepLoopState,
    step: StepDefinition,
    result: StepResult,
    step_index: int,
) -> tuple[str, StepResult | None, str | None]:
    """Apply effective terminal policy for a failed step result.

    Returns:
        ``(action, result_to_record, error_message)`` where action is one of
        ``retry``, ``continue``, ``abort``.
    """
    policy = effective_terminal_policy(step.on_failure)
    if policy == FailurePolicy.CONTINUE:
        # Defensive: step-local continue already maps to ignored; treat as non-fatal.
        return "continue", mark_continued_after_prompt(result), None
    if policy == FailurePolicy.PROMPT_USER:
        decision, warning = _prompt_user_decision(context, state, step, result, step_index)
        if warning is not None:
            state.warnings.append(warning)
        return _apply_prompt_decision(decision, result)
    return "abort", result, _failed_step_message(result)


def _execute_one_step(
    context: RunContext,
    state: StepLoopState,
    step: StepDefinition,
    *,
    idx: int,
    total: int,
    step_index: int,
    step_context: dict[str, object] | None,
    previous_step: PreviousStepMetadata | None = None,
    steps: Sequence[PreviousStepMetadata] | None = None,
    initial_attempt: int = 1,
) -> tuple[str, StepResult | None, str | None]:
    """Run a step until success, continue-after-failure, or abort.

    Returns ``(action, result, error_message)`` with action ``continue`` or ``abort``.
    """
    current_attempt = initial_attempt
    while True:
        _notify_step_start(context, idx, total, step)
        append_run_log_event(
            state.session_log_dir,
            RunLogEvent(event=RunLogEventType.STEP_START, step_index=idx, step_id=step.id, attempt=current_attempt),
        )
        on_output = (
            (lambda stream_name, line: _notify_step_output(context, idx, total, step, line, stream=stream_name))
            if context.observer is not None
            else None
        )
        result = StepExecution(
            StepExecutionContext(
                step=step,
                sandbox_path=state.target_dir,
                context=step_context,
                on_output=on_output,
                step_index=idx,
                initial_attempt=current_attempt,
                identity=context.identity,
                previous_step=previous_step,
                steps=steps,
                session_tmp_dir=state.session_tmp_dir,
                session_log_dir=state.session_log_dir,
                save_attempt_logs=state.save_attempt_logs,
                session_id=context.session_id or "",
                artifacts_dir=state.artifacts_dir,
                artifacts_db=state.artifacts_db,
            )
        ).run()
        _notify_step_done(context, idx, total, result)
        append_run_log_event(
            state.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.STEP_DONE,
                step_index=idx,
                step_id=step.id,
                attempt=result.attempts,
                status=result.status,
                exit_code=result.exit_code,
            ),
        )
        if result.ok:
            publish_warnings = auto_publish_step_artifacts(
                step,
                sandbox_path=state.target_dir,
                session_id=context.session_id or "",
                artifacts_dir=state.artifacts_dir,
                artifacts_db=state.artifacts_db,
            )
            state.warnings.extend(publish_warnings)
            return "continue", result, None
        action, recorded, error_message = _handle_failed_step(context, state, step, result, step_index)
        if action == "retry":
            current_attempt = result.attempts + 1
            continue
        return action, recorded, error_message


def _pending_result_for_resume(checkpoint: RunCheckpoint, step: StepDefinition) -> StepResult:
    """Return existing pending step result from checkpoint or construct fallback."""
    if checkpoint.pending_result is not None:
        return checkpoint.pending_result
    return StepResult(
        step_id=step.id,
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        error_message=checkpoint.diagnostic,
    )


def _resume_pending_gate(
    context: RunContext,
    state: StepLoopState,
    step: StepDefinition,
    checkpoint: RunCheckpoint,
    step_index: int,
    previous_step: PreviousStepMetadata | None = None,
    steps: Sequence[PreviousStepMetadata] | None = None,
) -> tuple[str, StepResult | None, str | None]:
    """Re-prompt at the paused step without re-executing it first."""
    result = _pending_result_for_resume(checkpoint, step)
    action, recorded, error_message = _handle_failed_step(context, state, step, result, step_index)
    if action != "retry":
        return action, recorded, error_message
    return _execute_one_step(
        context,
        state,
        step,
        idx=step_index + 1,
        total=len(context.steps),
        step_index=step_index,
        step_context=_build_step_context(context),
        previous_step=previous_step,
        steps=steps,
        initial_attempt=result.attempts + 1,
    )


def _find_loop_sub_step_name(loop: LoopStepBlock, step_id: str) -> str:
    """Locate display name for a sub-step ID inside a loop step block."""
    for sub_step in loop.do:
        if sub_step.id == step_id:
            return sub_step.name or ""
    return ""


def _find_step_name_by_id(steps: Sequence[StepDefinition | LoopStepBlock], step_id: str) -> str:
    """Find step display name matching step_id across step definitions and loops."""
    for candidate_step in steps:
        if candidate_step.id == step_id:
            return getattr(candidate_step, "name", None) or ""
        if isinstance(candidate_step, LoopStepBlock):
            found = _find_loop_sub_step_name(candidate_step, step_id)
            if found:
                return found
    return ""


def _resolve_historical_steps_metadata(
    context: RunContext,
    state: StepLoopState,
) -> list[PreviousStepMetadata]:
    """Build list of PreviousStepMetadata for all finished steps so far."""
    if not state.step_results:
        return []

    historical: list[PreviousStepMetadata] = []
    for idx, result in enumerate(state.step_results):
        step_index = idx + 1
        if idx < len(context.steps) and context.steps[idx].id == result.step_id:
            step_name = getattr(context.steps[idx], "name", None) or ""
        else:
            step_name = _find_step_name_by_id(context.steps, result.step_id)
        historical.append(
            previous_step_metadata_from_result(
                result,
                step_index=step_index,
                step_name=step_name,
            )
        )
    return historical


def _resolve_previous_step_metadata(
    context: RunContext,
    state: StepLoopState,
    step_index: int,
) -> PreviousStepMetadata:
    """Build PreviousStepMetadata for the step about to execute."""
    historical = _resolve_historical_steps_metadata(context, state)
    if not historical:
        return PreviousStepMetadata()
    return historical[-1]


def _dispatch_step(
    context: RunContext,
    state: StepLoopState,
    step: StepDefinition | LoopStepBlock,
    step_index: int,
    step_context: dict[str, object] | None,
) -> tuple[str, StepResult | None, str | None]:
    """Run or re-prompt a step or loop block depending on whether this is the resume gate."""
    if isinstance(step, LoopStepBlock):
        runner = LoopBlockRunner(
            loop=step,
            sandbox_path=state.target_dir,
            context=step_context,
            observer=context.observer,
            failure_prompter=context.failure_prompter,
            no_tty=context.no_tty,
            pause_store=context.pause_store,
            step_index=step_index + 1,
            identity=context.identity,
            resume_from=context.resume_from,
            session_tmp_dir=state.session_tmp_dir,
            session_log_dir=state.session_log_dir,
            save_attempt_logs=state.save_attempt_logs,
            session_id=context.session_id,
            artifacts_dir=state.artifacts_dir,
            artifacts_db=state.artifacts_db,
        )
        return runner.run(state)

    historical_steps = _resolve_historical_steps_metadata(context, state)
    previous_step = historical_steps[-1] if historical_steps else PreviousStepMetadata()
    resume = context.resume_from
    if resume is not None and step_index == resume.next_step_index:
        return _resume_pending_gate(
            context,
            state,
            step,
            resume,
            step_index,
            previous_step=previous_step,
            steps=historical_steps,
        )
    return _execute_one_step(
        context,
        state,
        step,
        idx=step_index + 1,
        total=len(context.steps),
        step_index=step_index,
        step_context=step_context,
        previous_step=previous_step,
        steps=historical_steps,
    )


def _run_remaining_steps(
    context: RunContext,
    state: StepLoopState,
    start: int,
) -> tuple[RunStatus, list[str]]:
    """Execute remaining steps from ``start`` until completion or abort."""
    step_context = _build_step_context(context)
    for step_index, step in enumerate(context.steps):
        if step_index < start:
            continue
        action, result, error_message = _dispatch_step(context, state, step, step_index, step_context)
        if result is not None:
            state.step_results.append(result)
        if action == "abort":
            errors = [error_message] if error_message else []
            return RunStatus.FAILED, errors
    return RunStatus.COMPLETED, []


def _run_step_loop(
    context: RunContext,
    state: StepLoopState,
) -> tuple[RunStatus, list[StepResult], list[str], list[str]]:
    """Execute all steps, honoring failure policies and cancellation."""
    start = context.resume_from.next_step_index if context.resume_from is not None else 0
    try:
        status, errors = _run_remaining_steps(context, state, start)
    except PromptUserInterruptedError as exc:
        errors = [str(exc)] if str(exc) else []
        return RunStatus.PAUSED, state.step_results, errors, state.warnings
    except KeyboardInterrupt:
        process_registry.terminate_all(grace_seconds=0.5)
        return RunStatus.CANCELLED, state.step_results, ["Execution cancelled by user."], state.warnings
    return status, state.step_results, errors, state.warnings


def _handle_auto_apply(
    context: RunContext,
    manager: Sandbox | None,
    session: SandboxSession | None,
    errors: list[str],
    warnings: list[str],
) -> tuple[RunStatus | None, bool]:
    """Apply sandbox changes on completed runs when auto_apply is enabled.

    Returns:
        Tuple of (new_status_or_None, apply_failed_boolean).
    """
    if not (context.auto_apply and session is not None and manager is not None):
        return None, False

    apply_result = manager.apply(
        session.session_id,
        strategy=SandboxApplyStrategy.PATCH,
    )
    warnings.extend(apply_result.warnings)
    if not apply_result.ok:
        errors.extend(apply_result.errors)
        return RunStatus.FAILED, True

    return None, False


def _capture_and_persist_diff(
    context: RunContext,
    session: SandboxSession | None,
    warnings: list[str],
) -> None:
    """Capture cumulative unified diff from sandbox and persist diff.patch."""
    if session is None or context.session_id is None or not Path(session.sandbox_path).is_dir():
        return

    session_id = context.session_id
    try:
        GitRunner.add_intent_to_add(session.sandbox_path, target=".")
        diff_text = GitRunner.diff(session.sandbox_path, base_commit=session.base_commit, binary=True)
        session_dir = get_session_dir(context.cwd, session_id)
        write_session_diff(session_dir, diff_text)
    except Exception as exc:
        warnings.append(f"Failed to persist session diff artifact: {exc}")


def _finalize_sandbox_cleanup(
    context: RunContext,
    manager: Sandbox | None,
    session: SandboxSession | None,
    target_dir: Path,
    status: RunStatus,
    apply_failed: bool,
) -> bool:
    """Clean up or keep the sandbox worktree based on run status."""
    if status == RunStatus.PAUSED or apply_failed:
        kept_path = session.sandbox_path if session is not None else target_dir
        _notify_sandbox_cleanup(context, kept=True, path=kept_path)
        return True

    return _cleanup_sandbox(context, manager, session, target_dir)


def _prepare_session_tmp_dir(context: RunContext, warnings: list[str]) -> Path | None:
    """Resolve and create the session scratch directory tree, or warn and return None."""
    if context.session_id is None:
        return None
    tmp_dir = resolve_project_filesystem_paths(context.cwd).tmp_dir
    session_tmp_dir = tmp_dir / context.session_id
    try:
        (session_tmp_dir / "steps").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"Failed to create session scratch directory: {exc}")
        return None
    return session_tmp_dir


def _prepare_session_artifacts(context: RunContext) -> tuple[Path | None, ArtifactsRepository | None]:
    """Resolve the project artifacts_dir and construct a per-run ArtifactsRepository once, or (None, None) without a session."""
    if context.session_id is None:
        return None, None
    artifacts_dir = resolve_project_filesystem_paths(context.cwd).artifacts_dir
    return artifacts_dir, ArtifactsRepository(context.cwd.resolve())


def _prepare_session_log_dir(context: RunContext, warnings: list[str]) -> Path | None:
    """Resolve and create the session log directory, or warn and return None."""
    if context.session_id is None:
        return None
    logs_dir = resolve_project_filesystem_paths(context.cwd).logs_dir
    session_log_dir = logs_dir / context.session_id
    try:
        session_log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"Failed to create session log directory: {exc}")
        return None
    return session_log_dir


def _cleanup_session_tmp_dir(session_tmp_dir: Path | None, *, keep: bool, status: RunStatus) -> None:
    """Best-effort delete of the session scratch directory on completed, unkept runs."""
    if session_tmp_dir is None or keep or status != RunStatus.COMPLETED:
        return
    try:
        shutil.rmtree(session_tmp_dir)
    except OSError:
        # Best-effort cleanup: scratch directory removal is independent of run outcome.
        pass


def run_steps(context: RunContext) -> RunOutcome:
    """Execute a sequence of steps under optional sandbox isolation and observer reporting.

    Args:
        context: Immutable run inputs including steps, cwd, and sandbox options.

    Returns:
        Classified outcome. Expected step/sandbox failures and cancellation are
        returned as structured status values rather than raised.
    """
    target_dir, manager, session, setup_error = _setup_sandbox(context)
    if setup_error is not None:
        return RunOutcome(
            status=RunStatus.FAILED,
            step_results=[],
            errors=[setup_error],
            sandbox_kept=False,
            sandbox_path=target_dir,
        )

    prior = list(context.resume_from.step_results) if context.resume_from is not None else []
    setup_warnings: list[str] = []
    session_tmp_dir = _prepare_session_tmp_dir(context, setup_warnings)
    session_log_dir = _prepare_session_log_dir(context, setup_warnings)
    artifacts_dir, artifacts_db = _prepare_session_artifacts(context)
    save_attempt_logs = context.config.history.save_attempt_logs if context.config is not None else True
    state = StepLoopState(
        target_dir=target_dir,
        session=session,
        step_results=prior,
        session_tmp_dir=session_tmp_dir,
        session_log_dir=session_log_dir,
        save_attempt_logs=save_attempt_logs,
        artifacts_dir=artifacts_dir,
        artifacts_db=artifacts_db,
    )
    append_run_log_event(
        session_log_dir,
        RunLogEvent(
            event=RunLogEventType.RUN_STARTED,
            session_id=context.session_id,
            blueprint_key=context.identity.blueprint_key if context.identity else None,
        ),
    )

    status: RunStatus = RunStatus.FAILED
    step_results: list[StepResult] = []
    errors: list[str] = []
    warnings: list[str] = []
    apply_failed = False
    try:
        status, step_results, errors, warnings = _run_step_loop(context, state)
        warnings = [*setup_warnings, *warnings]
        if status == RunStatus.COMPLETED:
            new_status, apply_failed = _handle_auto_apply(context, manager, session, errors, warnings)
            if new_status is not None:
                status = new_status
    finally:
        process_registry.terminate_all(grace_seconds=0.5)
        _capture_and_persist_diff(context, session, warnings)
        sandbox_kept = _finalize_sandbox_cleanup(context, manager, session, target_dir, status, apply_failed)
        _cleanup_session_tmp_dir(session_tmp_dir, keep=context.keep, status=status)
        append_run_log_event(session_log_dir, RunLogEvent(event=RunLogEventType.RUN_COMPLETED, status=status.value))

    return RunOutcome(
        status=status,
        step_results=step_results,
        errors=errors,
        warnings=warnings,
        sandbox_kept=sandbox_kept,
        sandbox_path=session.sandbox_path if session is not None else target_dir,
    )
