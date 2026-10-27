"""Class-based execution service for blueprint run commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from dovo.common.clock import Clock
from dovo.common.diagnostic_log import DiagnosticRecord, error_diagnostics
from dovo.common.exceptions import catch_failure
from dovo.common.filesystem import WorkspacePaths
from dovo.common.session_id import SessionIdFactory
from dovo.core.agents.models import AgentEnvMode, AgentEnvOverrides
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.db import SessionsRepository
from dovo.core.git.runner import GitRunner
from dovo.core.sessions import reconcile_stale_sessions
from dovo.engine.engine import Engine
from dovo.engine.models import (
    BlueprintRunResult,
    FailurePrompter,
    InteractionPrompter,
    RunObserver,
    RunOutcome,
    RunRequest,
)
from dovo.engine.services._shared import fail, finalize, resolve_env_overrides


@dataclass
class BlueprintRunService:
    """Service encapsulating the blueprint execution lifecycle."""

    name: str
    paths: WorkspacePaths
    sessions_db: SessionsRepository
    git: GitRunner
    clock: Clock
    new_id: SessionIdFactory
    no_worktree: bool = False
    keep: bool = False
    discard: bool = False
    agent: str | None = None
    session_id: str | None = None
    cli_args: list[str] | None = None
    no_tty: bool = False
    auto_apply: bool = False
    env_mode: AgentEnvMode | None = None
    env_passthrough: list[str] = field(default_factory=list)
    observer: RunObserver | None = None
    failure_prompter: FailurePrompter | None = None
    interaction_prompter: InteractionPrompter | None = None
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = field(default_factory=list)

    def execute(self) -> BlueprintRunResult:
        """Run the full execution pipeline and return the outcome, reporting any failure behind the engine.run catch.

        Raises:
            LockTimeoutError: If another process holds the workspace lock.
        """
        env_overrides, env_error = resolve_env_overrides(self.env_mode, self.env_passthrough)
        if env_error is not None:
            return fail(self.warnings, self.diagnostics, env_error)

        reconciliation = reconcile_stale_sessions(self.sessions_db, path=self.paths.root_dir)
        if reconciliation.warning:
            self.warnings.append(reconciliation.warning)
        self.diagnostics.extend(error_diagnostics(reconciliation.diagnostic))

        catalog = Catalog(self.paths)
        with catch_failure("engine.run", blueprint=self.name) as caught:
            blueprint = Blueprint.load(self.name, catalog=catalog)
            run_outcome = self._run(catalog, blueprint, env_overrides)
        if caught.failure is not None:
            return BlueprintRunResult(
                session_record=None,
                warnings=self.warnings,
                **caught.failure.result_fields(prior_diagnostics=self.diagnostics),
            )

        return finalize(self.sessions_db, self.warnings, self.diagnostics, run_outcome, run_outcome.session_id or "")

    def _run(self, catalog: Catalog, blueprint: Blueprint, env_overrides: AgentEnvOverrides | None) -> RunOutcome:
        """Execute blueprint through Engine with this service's run request."""
        return Engine(
            self.paths, db=self.sessions_db, catalog=catalog, git=self.git, clock=self.clock, new_id=self.new_id
        ).run(
            blueprint,
            RunRequest(
                cli_args=self.cli_args,
                use_worktree=not self.no_worktree,
                keep=self.keep,
                discard=self.discard,
                agent=self.agent,
                session_id=self.session_id,
                observer=self.observer,
                failure_prompter=self.failure_prompter,
                interaction_prompter=self.interaction_prompter,
                no_tty=self.no_tty,
                auto_apply=self.auto_apply,
                env_overrides=env_overrides,
            ),
        )
