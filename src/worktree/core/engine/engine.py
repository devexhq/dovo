"""Process handle: persist a run row and execute a Blueprint via run_steps."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from worktree.common.lock import WorkspaceLock
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.config import Config
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.engine.exceptions import EngineInputError
from worktree.core.engine.models import DefinitionsManifest, RunRequest, SessionRunPayload
from worktree.core.engine.resumable import ResumableRun
from worktree.core.engine.writer import get_session_dir, load_session_run, snapshot_definitions, write_session_run_json
from worktree.core.inputs import InputResolveResult
from worktree.core.runtime import (
    ExecutionIdentity,
    FailurePrompter,
    RunCheckpoint,
    RunContext,
    RunObserver,
    RunOutcome,
    RunPauseStore,
    run_steps,
)
from worktree.core.step import LoopStepBlock, StepDefinition


class _DbPauseStore:
    """``RunPauseStore`` adapter backed by RunsRepository."""

    def __init__(self, db: RunsRepository, session_id: str) -> None:
        self._db = db
        self._session_id = session_id

    def save_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        """Write checkpoint JSON and set the tracked run to paused."""
        self._db.save_pause(
            self._session_id,
            checkpoint.model_dump_json(),
            checkpoint.diagnostic,
        )

    def clear_pause(self) -> None:
        """Mark the tracked run running again after an in-process prompt returns."""
        self._db.update_status(self._session_id, RunStatus.RUNNING, pid=os.getpid())

    def finalize(self, status: RunStatus, error_message: str | None) -> None:
        """Write the terminal (or paused) status after ``run_steps`` returns."""
        self._db.update_status(self._session_id, status, error_message=error_message)


class Engine:
    """Process: sandbox, DB row, pause store, sequential step loop."""

    def __init__(
        self,
        path: Path,
        db: RunsRepository,
        catalog: Catalog,
    ) -> None:
        self.path = path.resolve()
        self.db = db
        self.catalog = catalog

    def run(self, blueprint: Blueprint, request: RunRequest | None = None) -> RunOutcome:
        """Adapt ``blueprint`` into ``RunContext`` and delegate to ``run_steps``."""
        req = request or RunRequest()
        steps = blueprint.steps
        resolved = self._resolve_run_inputs(blueprint, req)
        sid = req.session_id or f"blueprint_{uuid.uuid4().hex[:8]}"
        engine_warnings: list[str] = list(resolved.warnings)
        pause_store = self._start_run(blueprint, sid, engine_warnings)
        definitions = self._snapshot_definitions(blueprint, sid, engine_warnings)
        caller_sandbox = True if req.use_sandbox is None else req.use_sandbox
        identity = ExecutionIdentity(blueprint_name=blueprint.name, blueprint_key=blueprint.key)

        start_time = datetime.now(UTC).isoformat()
        outcome = self._execute(
            steps=steps,
            use_sandbox=caller_sandbox and blueprint.use_sandbox,
            keep=req.keep,
            agent=req.agent,
            observer=req.observer,
            inputs=resolved.values,
            identity=identity,
            session_id=sid,
            no_tty=req.no_tty,
            failure_prompter=req.failure_prompter,
            pause_store=pause_store,
            auto_apply=req.auto_apply,
        )

        if pause_store is not None:
            self._finish_run(
                pause_store,
                outcome,
                engine_warnings,
                blueprint=blueprint,
                started_at=start_time,
                definitions=definitions,
            )

        return self._finalize_outcome(outcome, sid, engine_warnings)

    def resume(
        self,
        session_id: str,
        *,
        blueprint: Blueprint | None = None,
        observer: RunObserver | None = None,
        failure_prompter: FailurePrompter | None = None,
        no_tty: bool = False,
    ) -> RunOutcome:
        """Classify a paused session, rebuild ``RunContext``, and re-enter ``run_steps``."""
        loaded, db, checkpoint = ResumableRun.load(
            session_id,
            blueprint,
            path=self.path,
            db=self.db,
            catalog=self.catalog,
        ).ready()
        steps = loaded.steps

        pause_store = _DbPauseStore(db, session_id)
        engine_warnings: list[str] = []
        self._mark_running(pause_store, engine_warnings)
        identity = ExecutionIdentity(blueprint_name=loaded.name, blueprint_key=loaded.key)

        start_time = datetime.now(UTC).isoformat()
        outcome = self._execute(
            steps=steps,
            use_sandbox=checkpoint.use_sandbox,
            keep=checkpoint.keep,
            agent=checkpoint.agent,
            observer=observer,
            inputs=checkpoint.inputs or None,
            identity=identity,
            session_id=session_id,
            no_tty=no_tty,
            failure_prompter=failure_prompter,
            pause_store=pause_store,
            resume_from=checkpoint,
        )

        prior_run = load_session_run(self.path, session_id)
        definitions = prior_run.definitions if prior_run is not None else None
        self._finish_run(
            pause_store,
            outcome,
            engine_warnings,
            blueprint=loaded,
            started_at=start_time,
            definitions=definitions,
        )

        return self._finalize_outcome(outcome, session_id, engine_warnings)

    def _execute(
        self,
        *,
        steps: list[StepDefinition | LoopStepBlock],
        use_sandbox: bool = True,
        keep: bool = False,
        agent: str | None = None,
        observer: RunObserver | None = None,
        inputs: dict[str, str | int | bool] | None = None,
        identity: ExecutionIdentity | None = None,
        session_id: str | None = None,
        no_tty: bool = False,
        failure_prompter: FailurePrompter | None = None,
        pause_store: RunPauseStore | None = None,
        resume_from: RunCheckpoint | None = None,
        auto_apply: bool = False,
    ) -> RunOutcome:
        """Build the RunContext both run() and resume() share and drive it through run_steps."""
        return run_steps(
            RunContext(
                steps=steps,
                cwd=self.path,
                use_sandbox=use_sandbox,
                keep=keep,
                agent=agent,
                observer=observer,
                inputs=inputs,
                identity=identity,
                session_id=session_id,
                no_tty=no_tty,
                failure_prompter=failure_prompter,
                pause_store=pause_store,
                resume_from=resume_from,
                auto_apply=auto_apply,
                config=Config(self.path)._loaded_config,
            )
        )

    def _start_run(
        self,
        blueprint: Blueprint,
        session_id: str,
        warnings: list[str],
    ) -> _DbPauseStore | None:
        """Insert a RUNNING row and return a pause store, or warn and skip persistence."""
        with WorkspaceLock(self.path):
            try:
                self._insert_running(blueprint, session_id)
            except Exception as exc:
                warnings.append(f"Failed to record run start in database: {exc}")
                return None

        return _DbPauseStore(self.db, session_id)

    def _snapshot_definitions(
        self,
        blueprint: Blueprint,
        session_id: str,
        warnings: list[str],
    ) -> DefinitionsManifest | None:
        """Snapshot the resolved blueprint and its uses: step references into the session directory."""
        session_dir = get_session_dir(self.path, session_id)
        return snapshot_definitions(self.catalog, blueprint, session_dir, warnings)

    def _persist_session_run_json(
        self,
        session_id: str,
        blueprint: Blueprint,
        outcome: RunOutcome,
        started_at: str,
        warnings: list[str],
        *,
        definitions: DefinitionsManifest | None = None,
    ) -> None:
        """Persist structured run results and step telemetry to run.json."""
        try:
            session_dir = get_session_dir(self.path, session_id)
            payload = SessionRunPayload(
                version=1,
                session_id=session_id,
                name=blueprint.name,
                status=outcome.status.value,
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
                error_message=outcome.errors[0] if outcome.errors else None,
                step_results=outcome.step_results,
                definitions=definitions,
            )
            write_session_run_json(session_dir, payload)
        except Exception as exc:
            warnings.append(f"Failed to persist session run json: {exc}")

    def _finish_run(
        self,
        pause_store: _DbPauseStore,
        outcome: RunOutcome,
        warnings: list[str],
        *,
        blueprint: Blueprint | None = None,
        started_at: str | None = None,
        definitions: DefinitionsManifest | None = None,
    ) -> None:
        """Persist the outcome status when the start insert succeeded."""
        with WorkspaceLock(self.path):
            try:
                error_message = outcome.errors[0] if outcome.errors else None
                pause_store.finalize(outcome.status, error_message)
            except Exception as exc:
                warnings.append(f"Failed to update run status in database: {exc}")

            if blueprint is not None and started_at is not None:
                self._persist_session_run_json(
                    pause_store._session_id,
                    blueprint,
                    outcome,
                    started_at,
                    warnings,
                    definitions=definitions,
                )

    def _insert_running(self, blueprint: Blueprint, session_id: str) -> None:
        """Insert a RUNNING row for the bound repository."""
        self.db.create(
            session_id=session_id,
            blueprint_name=blueprint.name,
            blueprint_key=blueprint.key,
            branch_name="",
            status=RunStatus.RUNNING,
            pid=os.getpid(),
        )

    def _resolve_run_inputs(self, blueprint: Blueprint, request: RunRequest) -> InputResolveResult:
        """Apply defaults and required checks; raise before a run row is inserted."""
        result = blueprint.resolve_inputs(request.cli_args, overrides=request.inputs)
        if not result.ok:
            raise EngineInputError(result)
        return result

    def _finalize_outcome(self, outcome: RunOutcome, session_id: str, extra_warnings: list[str]) -> RunOutcome:
        """Stamp the session id and append Engine warnings onto ``outcome``."""
        update: dict[str, object] = {"session_id": session_id}
        if extra_warnings:
            update["warnings"] = [*outcome.warnings, *extra_warnings]
        return outcome.model_copy(update=update)

    def _mark_running(self, pause_store: _DbPauseStore, warnings: list[str]) -> None:
        """Set the paused row back to running, or record a persistence warning."""
        with WorkspaceLock(self.path):
            try:
                pause_store.clear_pause()
            except Exception as exc:
                warnings.append(f"Failed to update run status in database: {exc}")
