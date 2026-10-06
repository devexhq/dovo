"""Class-based execution service for blueprint resume commands."""

from __future__ import annotations

from dataclasses import dataclass, field

from dovo.common.filesystem import WorkspacePaths
from dovo.core.catalog import Catalog
from dovo.core.db import SessionsRepository
from dovo.engine.engine import Engine
from dovo.engine.exceptions import EngineResumeError, EngineRuntimeError
from dovo.engine.models import BlueprintRunResult, FailurePrompter, RunObserver
from dovo.engine.services._shared import fail, finalize, load_record


@dataclass
class BlueprintResumeService:
    """Service encapsulating the paused session resume lifecycle."""

    paths: WorkspacePaths
    db: SessionsRepository
    session_id: str | None = None
    no_tty: bool = False
    observer: RunObserver | None = None
    failure_prompter: FailurePrompter | None = None
    warnings: list[str] = field(default_factory=list)

    def execute(self) -> BlueprintRunResult:
        """Find session if omitted, classify and resume via Engine."""
        target_session_id, resolve_error = self._resolve_target_session()
        if resolve_error is not None or not target_session_id:
            return fail(self.warnings, resolve_error or "No paused session found to resume.")

        catalog = Catalog(self.paths)

        try:
            run_outcome = Engine(self.paths, db=self.db, catalog=catalog).resume(
                target_session_id,
                observer=self.observer,
                failure_prompter=self.failure_prompter,
                no_tty=self.no_tty,
            )
        except (EngineResumeError, EngineRuntimeError) as exc:
            return fail(self.warnings, str(exc))

        return finalize(self.db, self.warnings, run_outcome, target_session_id)

    def _resolve_target_session(self) -> tuple[str, str | None]:
        """Resolve session ID to resume, defaulting to latest paused session."""
        if not self.session_id:
            record = self.db.get_latest_paused()
            if record is None:
                return "", "No paused session found to resume."
            return record.session_id, None

        record = load_record(self.db, self.warnings, self.session_id)
        return self.session_id, None
