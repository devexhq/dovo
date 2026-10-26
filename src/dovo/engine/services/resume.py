"""Class-based execution service for blueprint resume commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from dovo.common.clock import Clock
from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.filesystem import WorkspacePaths
from dovo.common.session_id import SessionIdFactory
from dovo.core.agents.models import AgentEnvMode
from dovo.core.catalog import Catalog
from dovo.core.db import SessionsRepository
from dovo.core.git.runner import GitRunner
from dovo.core.interaction import HeadlessAnswerInput
from dovo.engine.engine import Engine
from dovo.engine.exceptions import EngineResumeError, EngineRuntimeError
from dovo.engine.models import BlueprintRunResult, FailurePrompter, InteractionPrompter, RunObserver
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
        """Find session if omitted, classify and resume via Engine."""
        env_overrides, env_error = resolve_env_overrides(self.env_mode, self.env_passthrough)
        if env_error is not None:
            return fail(self.warnings, self.diagnostics, env_error)

        target_session_id, resolve_error = self._resolve_target_session()
        if resolve_error is not None or not target_session_id:
            return fail(self.warnings, self.diagnostics, resolve_error or "No paused session found to resume.")

        catalog = Catalog(self.paths)

        try:
            run_outcome = Engine(
                self.paths, db=self.db, catalog=catalog, git=self.git, clock=self.clock, new_id=self.new_id
            ).resume(
                target_session_id,
                observer=self.observer,
                failure_prompter=self.failure_prompter,
                interaction_prompter=self.interaction_prompter,
                answers=self.answers,
                no_tty=self.no_tty,
                env_overrides=env_overrides,
            )
        except (EngineResumeError, EngineRuntimeError) as exc:
            return fail(self.warnings, self.diagnostics, str(exc))

        return finalize(self.db, self.warnings, self.diagnostics, run_outcome, target_session_id)

    def _resolve_target_session(self) -> tuple[str, str | None]:
        """Resolve session ID to resume, defaulting to latest paused session."""
        if not self.session_id:
            record = self.db.get_latest_paused()
            if record is None:
                return "", "No paused session found to resume."
            return record.session_id, None

        record = load_record(self.db, self.warnings, self.session_id)
        return self.session_id, None
