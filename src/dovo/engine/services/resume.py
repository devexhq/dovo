"""Class-based execution service for blueprint resume commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from dovo.common.clock import Clock
from dovo.common.diagnostic_log import DiagnosticRecord, error_diagnostics
from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.common.session_id import SessionIdFactory
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.catalog import Catalog
from dovo.core.db import SessionsRepository
from dovo.core.git.runner import GitRunner
from dovo.core.interaction import HeadlessAnswerInput
from dovo.core.sessions import reconcile_stale_sessions
from dovo.engine.engine import Engine
from dovo.engine.models import BlueprintRunResult, FailurePrompter, InteractionPrompter, RunObserver, RunOutcome
from dovo.engine.services._shared import fail, finalize, load_record, resolve_env_overrides


@dataclass
class BlueprintResumeService:
    """Service encapsulating the paused session resume lifecycle."""

    paths: WorkspacePaths
    db: SessionsRepository
    git: GitRunner
    clock: Clock
    new_id: SessionIdFactory
    session_id: str | None = None
    no_tty: bool = False
    env_mode: AgentEnvMode | None = None
    env_passthrough: list[str] = field(default_factory=list)
    observer: RunObserver | None = None
    failure_prompter: FailurePrompter | None = None
    interaction_prompter: InteractionPrompter | None = None
    answers: list[HeadlessAnswerInput] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = field(default_factory=list)

    def execute(self) -> BlueprintRunResult:
        """Reconcile stale runs, find the session if omitted and resume it via Engine, reporting any failure behind the engine.resume_target or engine.resume catch.

        Reconciliation takes and releases the workspace lock before Engine.resume takes it, so a crashed run is
        paused, and resumable, before the target is resolved.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        env_overrides, env_error = resolve_env_overrides(self.env_mode, self.env_passthrough)
        if env_error is not None:
            return fail(self.warnings, self.diagnostics, env_error)

        reconciliation = reconcile_stale_sessions(self.db, path=self.paths.root_dir)
        if reconciliation.warning:
            self.warnings.append(reconciliation.warning)
        self.diagnostics.extend(error_diagnostics(reconciliation.diagnostic))

        with catch_failure("engine.resume_target", session_id=self.session_id) as caught:
            target_session_id = self._target_session_id()
        if caught.failure is not None:
            return BlueprintRunResult(
                session_record=None,
                warnings=self.warnings,
                **caught.failure.result_fields(prior_diagnostics=self.diagnostics),
            )
        if target_session_id is None:
            return fail(self.warnings, self.diagnostics, "No paused session found to resume.")

        with catch_failure("engine.resume", session_id=target_session_id) as caught:
            run_outcome = self._resume(Catalog(self.paths), target_session_id, env_overrides)
        if caught.failure is not None:
            return BlueprintRunResult(
                session_record=None,
                warnings=self.warnings,
                **caught.failure.result_fields(prior_diagnostics=self.diagnostics),
            )

        return finalize(self.db, self.warnings, self.diagnostics, run_outcome, target_session_id)

    def _target_session_id(self) -> str | None:
        """Return session_id after loading its record, or the latest paused session's id, or None when nothing is paused."""
        if self.session_id:
            load_record(self.db, self.warnings, self.diagnostics, self.session_id)
            return self.session_id
        record = self.db.get_latest_paused()
        return record.session_id if record is not None else None

    def _resume(self, catalog: Catalog, session_id: str, env_overrides: AgentEnvOverrides | None) -> RunOutcome:
        """Resume session_id through Engine and return its outcome."""
        return Engine(
            self.paths, db=self.db, catalog=catalog, git=self.git, clock=self.clock, new_id=self.new_id
        ).resume(
            session_id,
            observer=self.observer,
            failure_prompter=self.failure_prompter,
            interaction_prompter=self.interaction_prompter,
            answers=self.answers,
            no_tty=self.no_tty,
            env_overrides=env_overrides,
        )
