"""Run session lifecycle: RunSession owns a run's workspace, executes through RunCoordinator, and closes the workspace by outcome."""

from __future__ import annotations

from worktree.common.filesystem import WorkspacePaths
from worktree.common.process import process_registry
from worktree.core.config import Config
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.coordinator import RunCoordinator
from worktree.core.engine.models import FailurePrompter, RunContext, RunObserver, RunOutcome, RunSettings
from worktree.core.engine.notify import safe_notify
from worktree.core.engine.state_store import RunStateStore
from worktree.core.engine.workspace import Workspace
from worktree.core.logs import RunLogEvent, RunLogEventType, append_run_log_event
from worktree.core.sandbox import Sandbox, SandboxSession
from worktree.core.step import ExecutionIdentity


def drive_run(
    paths: WorkspacePaths,
    runs: RunsRepository,
    session_id: str,
    *,
    observer: RunObserver | None,
    prompter: FailurePrompter | None,
    no_tty: bool,
) -> RunOutcome:
    """Open the run's workspace from its row, execute it through RunCoordinator, close the workspace by outcome, and notify on_run_completed."""
    row = runs.get(session_id)
    if row is None:
        return RunOutcome(
            status=RunStatus.FAILED,
            errors=[f"Run '{session_id}' not found."],
            sandbox_path=paths.root_dir,
        )

    opened = RunSession.open(row, paths, runs, observer, prompter, no_tty)
    outcome = opened.run() if isinstance(opened, RunSession) else opened
    safe_notify(observer, "on_run_completed", outcome)

    return outcome


def _workspace_context(row: RunRecord, paths: WorkspacePaths, observer: RunObserver | None) -> RunSettings:
    """Build the Workspace input from the run row's use_sandbox, keep, auto_apply, sandbox_id, and blueprint identity."""
    return RunSettings(
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


class RunSession:
    """One run's lifecycle: owns its Workspace and the infrastructure it opened, executes through RunCoordinator, auto-applies changes, and closes by outcome."""

    def __init__(
        self,
        row: RunRecord,
        paths: WorkspacePaths,
        runs: RunsRepository,
        observer: RunObserver | None,
        prompter: FailurePrompter | None,
        workspace: Workspace,
        context: RunContext,
        manager: Sandbox | None,
        sandbox: SandboxSession | None,
        setup_warnings: list[str],
    ) -> None:
        """Bind the session to its run row, collaborators, and the infrastructure opened by RunSession.open."""
        self._row = row
        self._paths = paths
        self._runs = runs
        self._observer = observer
        self._prompter = prompter
        self._workspace = workspace
        self._context = context
        self._manager = manager
        self._sandbox = sandbox
        self._setup_warnings = setup_warnings
        self._apply_failed = False

    @classmethod
    def open(
        cls,
        row: RunRecord,
        paths: WorkspacePaths,
        runs: RunsRepository,
        observer: RunObserver | None,
        prompter: FailurePrompter | None,
        no_tty: bool,
    ) -> RunSession | RunOutcome:
        """Set up the sandbox and session directories and return the session, or a FAILED outcome on setup error."""
        workspace = Workspace(_workspace_context(row, paths, observer))
        target_dir, manager, sandbox, setup_error = workspace.setup()
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
        context = RunContext(
            session_id=row.session_id,
            paths=paths,
            target_dir=target_dir,
            session_tmp_dir=session_tmp_dir,
            session_log_dir=session_log_dir,
            artifacts_dir=artifacts_dir,
            artifacts_db=artifacts_db,
            sandbox=sandbox,
            no_tty=no_tty,
            save_attempt_logs=Config(paths).history.save_attempt_logs,
        )
        return cls(row, paths, runs, observer, prompter, workspace, context, manager, sandbox, setup_warnings)

    def run(self) -> RunOutcome:
        """Execute the run, auto-apply sandbox changes, and close the workspace, returning the final outcome."""
        append_run_log_event(
            self._context.session_log_dir,
            RunLogEvent(
                event=RunLogEventType.RUN_STARTED,
                session_id=self._row.session_id,
                blueprint_key=self._row.blueprint_key,
            ),
        )
        try:
            outcome = self._apply_sandbox_changes(self._execute())
        except BaseException:
            closed = self._close(RunOutcome(status=RunStatus.FAILED, sandbox_path=self._context.target_dir))
            safe_notify(self._observer, "on_run_completed", closed)
            raise

        return self._close(outcome)

    def _execute(self) -> RunOutcome:
        """Run the coordinator against the opened context, prefixing the setup warnings onto its outcome."""
        coordinator = RunCoordinator(
            RunStateStore(self._runs, self._paths, self._row.session_id), self._context, self._observer, self._prompter
        )
        if coordinator.load():
            safe_notify(self._observer, "on_run_started", coordinator.steps)
        outcome = coordinator.execute()
        return outcome.model_copy(update={"warnings": [*self._setup_warnings, *outcome.warnings]})

    def _apply_sandbox_changes(self, outcome: RunOutcome) -> RunOutcome:
        """Auto-apply sandbox changes on a completed run, returning the outcome (FAILED on conflict) and recording whether apply failed."""
        if outcome.status != RunStatus.COMPLETED:
            return outcome

        errors = list(outcome.errors)
        warnings = list(outcome.warnings)
        new_status, self._apply_failed = self._workspace.handle_auto_apply(
            self._manager, self._sandbox, errors, warnings
        )
        update: dict[str, object] = {"errors": errors, "warnings": warnings}
        if new_status is not None:
            update["status"] = new_status
        return outcome.model_copy(update=update)

    def _close(self, outcome: RunOutcome) -> RunOutcome:
        """Capture the diff, clean up or keep the sandbox and scratch directory, log RUN_COMPLETED, and return the final outcome."""
        process_registry.terminate_all(grace_seconds=0.5)
        warnings = list(outcome.warnings)
        self._workspace.capture_and_persist_diff(self._sandbox, warnings)
        sandbox_kept = self._workspace.finalize_cleanup(
            self._manager, self._sandbox, self._context.target_dir, outcome.status, self._apply_failed
        )
        self._workspace.cleanup_session_tmp_dir(
            self._context.session_tmp_dir, keep=self._row.keep, status=outcome.status
        )
        append_run_log_event(
            self._context.session_log_dir,
            RunLogEvent(event=RunLogEventType.RUN_COMPLETED, status=outcome.status.value),
        )
        return outcome.model_copy(update={"warnings": warnings, "sandbox_kept": sandbox_kept})
