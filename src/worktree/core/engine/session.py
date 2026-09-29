"""Run session lifecycle: open the workspace, execute through RunCoordinator, close the workspace by outcome."""

from __future__ import annotations

from dataclasses import dataclass

from worktree.common.filesystem import WorkspacePaths
from worktree.common.process import process_registry
from worktree.core.config import Config
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.context import RunSessionContext
from worktree.core.engine.coordinator import RunCoordinator
from worktree.core.engine.models import FailurePrompter, RunContext, RunObserver, RunOutcome
from worktree.core.engine.state_store import RunStateStore
from worktree.core.engine.workspace import Workspace
from worktree.core.logs import RunLogEvent, RunLogEventType, append_run_log_event
from worktree.core.sandbox import Sandbox, SandboxSession
from worktree.core.step import ExecutionIdentity


@dataclass(frozen=True)
class _OpenedSession:
    """Infrastructure created for one run: its context, sandbox manager, and setup warnings."""

    context: RunSessionContext
    manager: Sandbox | None
    session: SandboxSession | None
    setup_warnings: list[str]


def drive_run(
    paths: WorkspacePaths,
    runs: RunsRepository,
    session_id: str,
    *,
    observer: RunObserver | None,
    prompter: FailurePrompter | None,
    no_tty: bool,
) -> RunOutcome:
    """Open the run's workspace from its row, execute it through RunCoordinator, and close the workspace by outcome."""
    row = runs.get(session_id)
    if row is None:
        return RunOutcome(
            status=RunStatus.FAILED,
            errors=[f"Run '{session_id}' not found."],
            sandbox_path=paths.root_dir,
        )

    workspace = Workspace(_workspace_context(row, paths, observer))
    opened = _open_session(workspace, paths, session_id, no_tty)
    if isinstance(opened, RunOutcome):
        return opened

    append_run_log_event(
        opened.context.session_log_dir,
        RunLogEvent(event=RunLogEventType.RUN_STARTED, session_id=session_id, blueprint_key=row.blueprint_key),
    )
    apply_failed = False
    try:
        outcome = RunCoordinator(RunStateStore(runs, paths, session_id), opened.context, observer, prompter).execute()
        outcome = outcome.model_copy(update={"warnings": [*opened.setup_warnings, *outcome.warnings]})
        outcome, apply_failed = _apply_sandbox_changes(workspace, opened, outcome)
    except BaseException:
        failed = RunOutcome(status=RunStatus.FAILED, sandbox_path=opened.context.target_dir)
        _close_session(workspace, opened, failed, apply_failed, row.keep)
        raise

    return _close_session(workspace, opened, outcome, apply_failed, row.keep)


def _workspace_context(row: RunRecord, paths: WorkspacePaths, observer: RunObserver | None) -> RunContext:
    """Build the Workspace input from the run row's use_sandbox, keep, auto_apply, sandbox_id, and blueprint identity."""
    return RunContext(
        steps=[],
        cwd=paths.root_dir,
        use_sandbox=row.use_sandbox,
        keep=row.keep,
        observer=observer,
        identity=ExecutionIdentity(blueprint_name=row.blueprint_name, blueprint_key=row.blueprint_key),
        session_id=row.session_id,
        auto_apply=row.auto_apply,
        sandbox_id=row.sandbox_id if row.use_sandbox else None,
        paths=paths,
    )


def _open_session(
    workspace: Workspace,
    paths: WorkspacePaths,
    session_id: str,
    no_tty: bool,
) -> _OpenedSession | RunOutcome:
    """Set up the sandbox and session directories and return the RunSessionContext, or a FAILED outcome on setup error."""
    target_dir, manager, session, setup_error = workspace.setup()
    if setup_error is not None:
        return RunOutcome(
            status=RunStatus.FAILED,
            errors=[setup_error],
            sandbox_kept=False,
            sandbox_path=target_dir,
        )

    setup_warnings: list[str] = []
    session_tmp_dir = workspace.prepare_session_tmp_dir(setup_warnings)
    session_log_dir = workspace.prepare_session_log_dir(setup_warnings)
    artifacts_dir, artifacts_db = workspace.prepare_session_artifacts()
    context = RunSessionContext(
        session_id=session_id,
        paths=paths,
        target_dir=target_dir,
        session_tmp_dir=session_tmp_dir,
        session_log_dir=session_log_dir,
        artifacts_dir=artifacts_dir,
        artifacts_db=artifacts_db,
        sandbox=session,
        no_tty=no_tty,
        save_attempt_logs=Config(paths).history.save_attempt_logs,
    )
    return _OpenedSession(context=context, manager=manager, session=session, setup_warnings=setup_warnings)


def _apply_sandbox_changes(
    workspace: Workspace,
    opened: _OpenedSession,
    outcome: RunOutcome,
) -> tuple[RunOutcome, bool]:
    """Auto-apply sandbox changes on a completed run, returning the outcome (FAILED on conflict) and whether apply failed."""
    if outcome.status != RunStatus.COMPLETED:
        return outcome, False

    errors = list(outcome.errors)
    warnings = list(outcome.warnings)
    new_status, apply_failed = workspace.handle_auto_apply(opened.manager, opened.session, errors, warnings)
    update: dict[str, object] = {"errors": errors, "warnings": warnings}
    if new_status is not None:
        update["status"] = new_status
    return outcome.model_copy(update=update), apply_failed


def _close_session(
    workspace: Workspace,
    opened: _OpenedSession,
    outcome: RunOutcome,
    apply_failed: bool,
    keep: bool,
) -> RunOutcome:
    """Capture the diff, clean up or keep the sandbox and scratch directory, log RUN_COMPLETED, and return the final outcome."""
    process_registry.terminate_all(grace_seconds=0.5)
    warnings = list(outcome.warnings)
    workspace.capture_and_persist_diff(opened.session, warnings)
    sandbox_kept = workspace.finalize_cleanup(
        opened.manager, opened.session, opened.context.target_dir, outcome.status, apply_failed
    )
    workspace.cleanup_session_tmp_dir(opened.context.session_tmp_dir, keep=keep, status=outcome.status)
    append_run_log_event(
        opened.context.session_log_dir,
        RunLogEvent(event=RunLogEventType.RUN_COMPLETED, status=outcome.status.value),
    )
    return outcome.model_copy(update={"warnings": warnings, "sandbox_kept": sandbox_kept})
