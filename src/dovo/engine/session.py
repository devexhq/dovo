"""Run session lifecycle: SessionRunner owns a run's workspace, executes through RunCoordinator, and closes the workspace by outcome."""

from __future__ import annotations

from dovo.common.filesystem import WorkspacePaths
from dovo.common.process import process_registry
from dovo.core.agents import default_tool_policy
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides, ResolvedAgentSettings
from dovo.core.config import Config
from dovo.core.config.models import AgentConfig
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.sessions import SessionLogEvent, SessionLogEventType
from dovo.core.worktree import Worktree, WorktreeSession
from dovo.engine.coordinator import RunCoordinator
from dovo.engine.models import (
    AgentSettingsResolution,
    FailurePrompter,
    RunContext,
    RunObserver,
    RunOutcome,
    RunSettings,
)
from dovo.engine.notify import safe_notify
from dovo.engine.session_log import append_session_log_event
from dovo.engine.state_store import SessionStateStore
from dovo.engine.workspace import Workspace


def drive_run(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    session_id: str,
    *,
    observer: RunObserver | None,
    prompter: FailurePrompter | None,
    no_tty: bool,
    env_overrides: AgentEnvOverrides | None = None,
) -> RunOutcome:
    """Drive one run to completion and notify on_run_completed.

    A config failure while resolving agent settings fails the run before any worktree is created.
    """
    row = sessions.get(session_id)
    if row is None:
        return RunOutcome(
            status=SessionStatus.FAILED,
            errors=[f"Session '{session_id}' not found."],
            worktree_path=paths.root_dir,
        )

    resolution = _resolve_agent_settings(paths, row.agent, env_overrides)
    if resolution.settings is None:
        outcome = RunOutcome(status=SessionStatus.FAILED, errors=list(resolution.errors), worktree_path=paths.root_dir)
        safe_notify(observer, "on_run_completed", outcome)
        return outcome

    opened = SessionRunner.open(row, paths, sessions, observer, prompter, no_tty, resolution.settings)
    outcome = opened.run() if isinstance(opened, SessionRunner) else opened
    safe_notify(observer, "on_run_completed", outcome)

    return outcome


def _resolve_agent_settings(
    paths: WorkspacePaths, override: str | None, env_overrides: AgentEnvOverrides | None
) -> AgentSettingsResolution:
    """Resolve agent settings; the run-row override replaces only the provider and env_overrides layer over config."""
    result = Config(paths).load()
    if not result.ok or result.config is None:
        return AgentSettingsResolution(errors=list(result.errors) or ["Failed to resolve agent settings."])

    agent = result.config.agent
    env_passthrough, env_mode = _merge_env_settings(agent, env_overrides)
    return AgentSettingsResolution(
        settings=ResolvedAgentSettings(
            provider=override or agent.provider,
            model=agent.model,
            endpoint=agent.endpoint,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            env_passthrough=env_passthrough,
            env_mode=env_mode,
            tools=agent.tools if agent.tools is not None else default_tool_policy(),
        )
    )


def _merge_env_settings(agent: AgentConfig, env_overrides: AgentEnvOverrides | None) -> tuple[list[str], AgentEnvMode]:
    """Return config passthrough followed by flag entries, de-duplicated in order, and the flag mode or the config mode."""
    if env_overrides is None:
        return list(agent.env_passthrough), agent.env_mode

    merged = list(dict.fromkeys([*agent.env_passthrough, *env_overrides.env_passthrough]))
    return merged, env_overrides.env_mode or agent.env_mode


def _workspace_context(
    row: SessionRecord, paths: WorkspacePaths, observer: RunObserver | None, sensitive_variables: tuple[str, ...]
) -> RunSettings:
    """Build the Workspace input from the session row and the configured sensitive variable names."""
    return RunSettings(
        cwd=paths.root_dir,
        use_worktree=row.use_worktree,
        keep=row.keep,
        observer=observer,
        session_id=row.session_id,
        auto_apply=row.auto_apply,
        worktree_id=row.worktree_id if row.use_worktree else None,
        sensitive_variables=sensitive_variables,
        paths=paths,
    )


class SessionRunner:
    """One run's lifecycle: owns its Workspace and the infrastructure it opened, executes through RunCoordinator, auto-applies changes, and closes by outcome."""

    def __init__(
        self,
        row: SessionRecord,
        paths: WorkspacePaths,
        sessions: SessionsRepository,
        observer: RunObserver | None,
        prompter: FailurePrompter | None,
        workspace: Workspace,
        context: RunContext,
        manager: Worktree | None,
        worktree: WorktreeSession | None,
        setup_warnings: list[str],
        agent: ResolvedAgentSettings,
    ) -> None:
        """Bind the session to its session row, collaborators, opened infrastructure, and resolved agent settings."""
        self._row = row
        self._paths = paths
        self._sessions = sessions
        self._observer = observer
        self._prompter = prompter
        self._workspace = workspace
        self._context = context
        self._manager = manager
        self._worktree = worktree
        self._setup_warnings = setup_warnings
        self._agent = agent
        self._apply_failed = False

    @classmethod
    def open(
        cls,
        row: SessionRecord,
        paths: WorkspacePaths,
        sessions: SessionsRepository,
        observer: RunObserver | None,
        prompter: FailurePrompter | None,
        no_tty: bool,
        agent: ResolvedAgentSettings,
    ) -> SessionRunner | RunOutcome:
        """Set up the worktree and session directories and return the session, or a FAILED outcome on setup error."""
        config = Config(paths)
        sensitive_variables = tuple(config.environment.sensitive_variables)
        workspace = Workspace(_workspace_context(row, paths, observer, sensitive_variables))
        target_dir, manager, worktree, setup_error = workspace.setup()
        if setup_error is not None:
            return RunOutcome(
                status=SessionStatus.FAILED,
                errors=[setup_error],
                worktree_kept=False,
                worktree_path=target_dir,
            )

        setup_warnings: list[str] = []
        link_error = workspace.link_session_dir(manager, worktree, setup_warnings)
        if link_error is not None:
            return RunOutcome(
                status=SessionStatus.FAILED,
                errors=[link_error],
                worktree_kept=False,
                worktree_path=paths.root_dir,
            )

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
            worktree=worktree,
            no_tty=no_tty,
            save_attempt_logs=config.history.save_attempt_logs,
            sensitive_variables=sensitive_variables,
        )
        return cls(
            row, paths, sessions, observer, prompter, workspace, context, manager, worktree, setup_warnings, agent
        )

    def run(self) -> RunOutcome:
        """Execute the run, auto-apply worktree changes, and close the workspace, returning the final outcome."""
        append_session_log_event(
            self._context.session_log_dir,
            SessionLogEvent(
                event=SessionLogEventType.SESSION_STARTED,
                session_id=self._row.session_id,
                blueprint_key=self._row.blueprint_key,
            ),
        )
        try:
            outcome = self._apply_worktree_changes(self._execute())
        except BaseException:
            closed = self._close(RunOutcome(status=SessionStatus.FAILED, worktree_path=self._context.target_dir))
            safe_notify(self._observer, "on_run_completed", closed)
            raise

        return self._close(outcome)

    def _execute(self) -> RunOutcome:
        """Run the coordinator against the opened context, prefixing the setup warnings onto its outcome."""
        coordinator = RunCoordinator(
            SessionStateStore(self._sessions, self._paths, self._row.session_id),
            self._context,
            self._observer,
            self._prompter,
            self._agent,
        )
        if coordinator.load():
            safe_notify(self._observer, "on_run_started", coordinator.steps)
        outcome = coordinator.execute()
        return outcome.model_copy(update={"warnings": [*self._setup_warnings, *outcome.warnings]})

    def _apply_worktree_changes(self, outcome: RunOutcome) -> RunOutcome:
        """Auto-apply worktree changes on a completed run, returning the outcome (FAILED on conflict) and recording whether apply failed."""
        if outcome.status != SessionStatus.COMPLETED:
            return outcome

        errors = list(outcome.errors)
        warnings = list(outcome.warnings)
        new_status, self._apply_failed = self._workspace.handle_auto_apply(
            self._manager, self._worktree, errors, warnings
        )
        update: dict[str, object] = {"errors": errors, "warnings": warnings}
        if new_status is not None:
            update["status"] = new_status
        return outcome.model_copy(update=update)

    def _close(self, outcome: RunOutcome) -> RunOutcome:
        """Capture the diff, clean up or keep the worktree and scratch directory, log SESSION_COMPLETED, and return the final outcome."""
        process_registry.terminate_all(grace_seconds=0.5)
        warnings = list(outcome.warnings)
        self._workspace.capture_and_persist_diff(self._worktree, warnings)
        worktree_kept = self._workspace.finalize_cleanup(
            self._manager, self._worktree, self._context.target_dir, outcome.status, self._apply_failed, warnings
        )
        self._workspace.cleanup_session_tmp_dir(
            self._context.session_tmp_dir, keep=self._row.keep, status=outcome.status
        )
        append_session_log_event(
            self._context.session_log_dir,
            SessionLogEvent(event=SessionLogEventType.SESSION_COMPLETED, status=outcome.status.value),
        )
        return outcome.model_copy(update={"warnings": warnings, "worktree_kept": worktree_kept})
