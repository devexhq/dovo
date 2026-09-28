"""Shared multi-step execution engine with optional sandbox lifecycle."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from worktree.common.process import process_registry
from worktree.core.db import RunStatus
from worktree.core.runtime.exceptions import PromptUserInterruptedError
from worktree.core.runtime.log_writer import append_run_log_event
from worktree.core.runtime.loop_runner import LoopBlockRunner
from worktree.core.runtime.models import (
    RunContext,
    RunLogEvent,
    RunLogEventType,
    RunOutcome,
    StepLoopState,
)
from worktree.core.runtime.step_coordinator import StepCoordinator
from worktree.core.runtime.workspace import Workspace
from worktree.core.step import (
    LoopStepBlock,
    PreviousStepMetadata,
    StepDefinition,
    StepResult,
)
from worktree.core.step.services.metadata import previous_step_metadata_from_result


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


def _dispatch_step(
    context: RunContext,
    state: StepLoopState,
    coordinator: StepCoordinator,
    loop_coordinator: StepCoordinator,
    step: StepDefinition | LoopStepBlock,
    step_index: int,
    step_context: dict[str, object] | None,
) -> tuple[str, list[StepResult], str | None]:
    """Run or re-prompt a step or loop block depending on whether this is the resume gate."""
    if isinstance(step, LoopStepBlock):
        runner = LoopBlockRunner(
            loop=step,
            sandbox_path=state.target_dir,
            coordinator=loop_coordinator,
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
        action, result, error_message = coordinator.resume_pending_gate(
            state,
            step,
            resume,
            step_index,
            previous_step=previous_step,
            steps=historical_steps,
        )
        return action, [result] if result is not None else [], error_message
    action, result, error_message = coordinator.execute_one_step(
        state,
        step,
        idx=step_index + 1,
        total=len(context.steps),
        step_index=step_index,
        step_context=step_context,
        previous_step=previous_step,
        steps=historical_steps,
    )
    return action, [result] if result is not None else [], error_message


def _run_remaining_steps(
    context: RunContext,
    state: StepLoopState,
    coordinator: StepCoordinator,
    loop_coordinator: StepCoordinator,
    start: int,
) -> tuple[RunStatus, list[str]]:
    """Execute remaining steps from ``start`` until completion or abort."""
    step_context = coordinator.build_step_context()
    for step_index, step in enumerate(context.steps):
        if step_index < start:
            continue
        action, results, error_message = _dispatch_step(
            context, state, coordinator, loop_coordinator, step, step_index, step_context
        )
        state.step_results.extend(results)
        if action == "abort":
            errors = [error_message] if error_message else []
            return RunStatus.FAILED, errors
    return RunStatus.COMPLETED, []


def _run_step_loop(
    context: RunContext,
    state: StepLoopState,
    coordinator: StepCoordinator,
    loop_coordinator: StepCoordinator,
) -> tuple[RunStatus, list[StepResult], list[str], list[str]]:
    """Execute all steps, honoring failure policies and cancellation."""
    start = context.resume_from.next_step_index if context.resume_from is not None else 0
    try:
        status, errors = _run_remaining_steps(context, state, coordinator, loop_coordinator, start)
    except PromptUserInterruptedError as exc:
        errors = [str(exc)] if str(exc) else []
        return RunStatus.PAUSED, state.step_results, errors, state.warnings
    except KeyboardInterrupt:
        process_registry.terminate_all(grace_seconds=0.5)
        return RunStatus.CANCELLED, state.step_results, ["Execution cancelled by user."], state.warnings
    return status, state.step_results, errors, state.warnings


def run_steps(context: RunContext) -> RunOutcome:
    """Execute a sequence of steps under optional sandbox isolation and observer reporting.

    Args:
        context: Immutable run inputs including steps, cwd, and sandbox options.

    Returns:
        Classified outcome. Expected step/sandbox failures and cancellation are
        returned as structured status values rather than raised.
    """
    workspace = Workspace(context)
    coordinator = StepCoordinator(context)
    loop_coordinator = StepCoordinator(dataclasses.replace(context, pause_store=None))

    target_dir, manager, session, setup_error = workspace.setup()
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
    session_tmp_dir = workspace.prepare_session_tmp_dir(setup_warnings)
    session_log_dir = workspace.prepare_session_log_dir(setup_warnings)
    artifacts_dir, artifacts_db = workspace.prepare_session_artifacts()
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
        status, step_results, errors, warnings = _run_step_loop(context, state, coordinator, loop_coordinator)
        warnings = [*setup_warnings, *warnings]
        if status == RunStatus.COMPLETED:
            new_status, apply_failed = workspace.handle_auto_apply(manager, session, errors, warnings)
            if new_status is not None:
                status = new_status
    finally:
        process_registry.terminate_all(grace_seconds=0.5)
        workspace.capture_and_persist_diff(session, warnings)
        sandbox_kept = workspace.finalize_cleanup(manager, session, target_dir, status, apply_failed)
        workspace.cleanup_session_tmp_dir(session_tmp_dir, keep=context.keep, status=status)
        append_run_log_event(session_log_dir, RunLogEvent(event=RunLogEventType.RUN_COMPLETED, status=status.value))

    return RunOutcome(
        status=status,
        step_results=step_results,
        errors=errors,
        warnings=warnings,
        sandbox_kept=sandbox_kept,
        sandbox_path=session.sandbox_path if session is not None else target_dir,
    )
