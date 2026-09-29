"""Process handle: persist a run row and execute a Blueprint via run_steps."""

from __future__ import annotations

import json
import os
import uuid

from worktree.common.filesystem import WorkspacePaths
from worktree.common.lock import WorkspaceLock
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.config import Config
from worktree.core.db import RunsRepository, RunStatus
from worktree.core.engine.exceptions import EngineInputError
from worktree.core.engine.models import DefinitionsManifest, RunRequest, RunStartConfig
from worktree.core.engine.resumable import ResumableRun
from worktree.core.engine.state_models import RunStateLoadStatus
from worktree.core.engine.state_store import RunStateStore
from worktree.core.engine.writer import get_session_dir, snapshot_definitions
from worktree.core.git.exceptions import GitError
from worktree.core.git.runner import GitRunner
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

    @property
    def session_id(self) -> str:
        """Return the tracked run's session id."""
        return self._session_id

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

    def finalize(self, status: RunStatus, error_message: str | None, sandbox_id: str | None = None) -> None:
        """Write the terminal (or paused) status and sandbox id after ``run_steps`` returns."""
        self._db.update_status(self._session_id, status, error_message=error_message, sandbox_id=sandbox_id)


class Engine:
    """Process: sandbox, DB row, pause store, sequential step loop."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: RunsRepository,
        catalog: Catalog,
    ) -> None:
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.catalog = catalog

    def run(self, blueprint: Blueprint, request: RunRequest | None = None) -> RunOutcome:
        """Adapt ``blueprint`` into ``RunContext`` and delegate to ``run_steps``."""
        req = request or RunRequest()
        steps = blueprint.steps
        resolved = self._resolve_run_inputs(blueprint, req)
        sid = req.session_id or f"blueprint_{uuid.uuid4().hex[:8]}"
        engine_warnings: list[str] = list(resolved.warnings)
        manifest = self._snapshot_definitions(blueprint, sid, engine_warnings)
        caller_sandbox = True if req.use_sandbox is None else req.use_sandbox
        effective_sandbox = caller_sandbox and blueprint.use_sandbox
        config = self._build_start_config(req, resolved.values, effective_sandbox, manifest)
        pause_store = self._start_run(blueprint, sid, config, manifest, engine_warnings)
        identity = ExecutionIdentity(blueprint_name=blueprint.name, blueprint_key=blueprint.key)

        outcome = self._execute(
            steps=steps,
            use_sandbox=effective_sandbox,
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
            self._finish_run(pause_store, outcome, engine_warnings)

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
        with WorkspaceLock(self.paths.lock_file):
            handle = ResumableRun.load(
                session_id,
                blueprint,
                paths=self.paths,
                db=self.db,
                catalog=self.catalog,
            )
        loaded, db, checkpoint = handle.ready()
        steps = loaded.steps

        pause_store = _DbPauseStore(db, session_id)
        engine_warnings: list[str] = []
        self._mark_running(pause_store, engine_warnings)
        identity = ExecutionIdentity(blueprint_name=loaded.name, blueprint_key=loaded.key)

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
            auto_apply=handle.auto_apply,
        )

        self._finish_run(pause_store, outcome, engine_warnings)

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
                config=Config(self.paths)._loaded_config,
                paths=self.paths,
            )
        )

    def _start_run(
        self,
        blueprint: Blueprint,
        session_id: str,
        config: RunStartConfig,
        manifest: DefinitionsManifest | None,
        warnings: list[str],
    ) -> _DbPauseStore | None:
        """Insert a RUNNING row and, when a manifest exists, its initial execution state, or warn and skip persistence."""
        with WorkspaceLock(self.paths.lock_file):
            try:
                self._insert_running(blueprint, session_id, config)
            except Exception as exc:
                warnings.append(f"Failed to record run start in database: {exc}")
                return None

            if manifest is not None:
                try:
                    self._initialize_state(blueprint, session_id, manifest, warnings)
                except Exception as exc:
                    warnings.append(f"Failed to initialize run state: {exc}")

        return _DbPauseStore(self.db, session_id)

    def _initialize_state(
        self,
        blueprint: Blueprint,
        session_id: str,
        manifest: DefinitionsManifest,
        warnings: list[str],
    ) -> None:
        """Commit the initial execution-state tree for the inserted row, appending a warning on failure."""
        result = RunStateStore(self.db, self.paths, session_id).initialize(blueprint, manifest)
        warnings.extend(result.warnings)
        if not result.ok:
            warnings.append(result.errors[0] if result.errors else "Failed to initialize run state.")

    def _snapshot_definitions(
        self,
        blueprint: Blueprint,
        session_id: str,
        warnings: list[str],
    ) -> DefinitionsManifest | None:
        """Snapshot the resolved blueprint and its uses: step references into the session directory."""
        session_dir = get_session_dir(self.paths, session_id)
        return snapshot_definitions(self.catalog, blueprint, session_dir, warnings)

    def _build_start_config(
        self,
        request: RunRequest,
        resolved_values: dict[str, str | int | bool],
        use_sandbox: bool,
        manifest: DefinitionsManifest | None,
    ) -> RunStartConfig:
        """Assemble the run-row configuration from the request, resolved inputs, and manifest."""
        return RunStartConfig(
            blueprint_tier=manifest.blueprint.ref.split(":", 1)[0] if manifest is not None else None,
            commit_sha=self._head_commit_sha(),
            use_sandbox=use_sandbox,
            keep=request.keep,
            agent=request.agent,
            inputs=dict(resolved_values),
            auto_apply=request.auto_apply,
        )

    def _head_commit_sha(self) -> str | None:
        """Return the repository HEAD commit SHA, or None when it cannot be resolved."""
        try:
            return GitRunner.rev_parse(self.path)
        except GitError:
            return None

    def _finalize_row(self, pause_store: _DbPauseStore, outcome: RunOutcome, warnings: list[str]) -> None:
        """Persist the outcome through the state store, or through the pause store when the row has no state."""
        error_message = outcome.errors[0] if outcome.errors else None
        store = RunStateStore(self.db, self.paths, pause_store.session_id)
        loaded = store.load()
        if not loaded.ok or loaded.state is None:
            if loaded.status != RunStateLoadStatus.MISSING_STATE and loaded.errors:
                warnings.append(loaded.errors[0])
            pause_store.finalize(outcome.status, error_message, outcome.sandbox_id)
            return

        saved = store.save(
            loaded.state,
            run_status=outcome.status,
            error_message=error_message,
            sandbox_id=outcome.sandbox_id,
        )
        warnings.extend([*loaded.warnings, *saved.warnings])
        if not saved.ok:
            warnings.append(saved.errors[0])

    def _finish_run(self, pause_store: _DbPauseStore, outcome: RunOutcome, warnings: list[str]) -> None:
        """Finalize the row under the workspace lock, recording persistence failures as warnings."""
        with WorkspaceLock(self.paths.lock_file):
            try:
                self._finalize_row(pause_store, outcome, warnings)
            except Exception as exc:
                warnings.append(f"Failed to update run status in database: {exc}")

    def _insert_running(self, blueprint: Blueprint, session_id: str, config: RunStartConfig) -> None:
        """Insert a RUNNING row carrying the resolved run configuration."""
        self.db.create(
            session_id=session_id,
            blueprint_name=blueprint.name,
            blueprint_key=blueprint.key,
            branch_name="",
            status=RunStatus.RUNNING,
            pid=os.getpid(),
            blueprint_tier=config.blueprint_tier,
            commit_sha=config.commit_sha,
            use_sandbox=config.use_sandbox,
            keep=config.keep,
            agent=config.agent,
            inputs_json=json.dumps(config.inputs, sort_keys=True),
            auto_apply=config.auto_apply,
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
        with WorkspaceLock(self.paths.lock_file):
            try:
                pause_store.clear_pause()
            except Exception as exc:
                warnings.append(f"Failed to update run status in database: {exc}")
