"""Process handle: persist a run row and execute a Blueprint through RunCoordinator."""

from __future__ import annotations

import json
import os
import uuid

from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.db import RunsRepository, RunStatus
from dovo.core.git.exceptions import GitError
from dovo.core.git.runner import GitRunner
from dovo.core.inputs import InputResolveResult
from dovo.engine.exceptions import EngineInputError
from dovo.engine.loader import EngineLoader
from dovo.engine.models import (
    DefinitionsManifest,
    FailurePrompter,
    RunObserver,
    RunOutcome,
    RunRequest,
    RunStartConfig,
)
from dovo.engine.session import drive_run
from dovo.engine.state_models import RunStateLoadStatus
from dovo.engine.state_store import RunStateStore
from dovo.engine.writer import get_session_dir, snapshot_definitions


class Engine:
    """Process: DB row, execution state, and the RunCoordinator-driven step loop."""

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
        """Persist the run row and initial state for ``blueprint``, then execute it through ``drive_run``."""
        req = request or RunRequest()
        resolved = self._resolve_run_inputs(blueprint, req)
        sid = req.session_id or f"blueprint_{uuid.uuid4().hex[:8]}"
        engine_warnings: list[str] = list(resolved.warnings)
        snapshot_warnings: list[str] = []
        manifest = self._snapshot_definitions(blueprint, sid, snapshot_warnings)
        if manifest is None:
            return self._failed_outcome(sid, snapshot_warnings, engine_warnings)

        engine_warnings.extend(snapshot_warnings)
        caller_worktree = True if req.use_worktree is None else req.use_worktree
        effective_worktree = caller_worktree and blueprint.use_worktree
        config = self._build_start_config(req, resolved.values, effective_worktree, manifest)
        start_failure = self._start_run(blueprint, sid, config, manifest)
        if start_failure is not None:
            return self._failed_outcome(sid, [start_failure], engine_warnings)

        outcome = drive_run(
            self.paths,
            self.db,
            sid,
            observer=req.observer,
            prompter=req.failure_prompter,
            no_tty=req.no_tty,
        )
        self._finish_run(sid, outcome, engine_warnings)

        return self._finalize_outcome(outcome, sid, engine_warnings)

    def resume(
        self,
        session_id: str,
        *,
        observer: RunObserver | None = None,
        failure_prompter: FailurePrompter | None = None,
        no_tty: bool = False,
    ) -> RunOutcome:
        """Load a paused run's durable state and execute it through the same RunCoordinator entry point as run()."""
        engine_warnings: list[str] = []
        with WorkspaceLock(self.paths.lock_file):
            EngineLoader.load_for_resume(self.db, self.paths, session_id)
            self._mark_running(session_id, engine_warnings)

        outcome = drive_run(
            self.paths,
            self.db,
            session_id,
            observer=observer,
            prompter=failure_prompter,
            no_tty=no_tty,
        )
        self._finish_run(session_id, outcome, engine_warnings)

        return self._finalize_outcome(outcome, session_id, engine_warnings)

    def _start_run(
        self,
        blueprint: Blueprint,
        session_id: str,
        config: RunStartConfig,
        manifest: DefinitionsManifest,
    ) -> str | None:
        """Insert the RUNNING row and its initial execution state under the workspace lock; return a failure message or None."""
        with WorkspaceLock(self.paths.lock_file):
            try:
                self._insert_running(blueprint, session_id, config)
            except Exception as exc:
                return f"Failed to record run start in database: {exc}"

            try:
                initialized = RunStateStore(self.db, self.paths, session_id).initialize(blueprint, manifest)
            except Exception as exc:
                message = f"Failed to initialize run state: {exc}"
            else:
                if initialized.ok:
                    return None
                message = initialized.errors[0] if initialized.errors else "Failed to initialize run state."

            try:
                self.db.update_status(session_id, RunStatus.FAILED, error_message=message)
            except Exception:
                # Best-effort: the start failure is already reported to the caller.
                pass
            return message

    def _failed_outcome(self, session_id: str, errors: list[str], warnings: list[str]) -> RunOutcome:
        """Build the FAILED RunOutcome for a run that could not start."""
        return RunOutcome(
            status=RunStatus.FAILED,
            errors=errors,
            warnings=warnings,
            worktree_path=self.path,
            session_id=session_id,
        )

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
        use_worktree: bool,
        manifest: DefinitionsManifest,
    ) -> RunStartConfig:
        """Assemble the run-row configuration from the request, resolved inputs, and manifest."""
        return RunStartConfig(
            blueprint_tier=manifest.blueprint.ref.split(":", 1)[0],
            commit_sha=self._head_commit_sha(),
            use_worktree=use_worktree,
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

    def _finalize_row(self, session_id: str, outcome: RunOutcome, warnings: list[str]) -> None:
        """Persist the outcome through the state store, or straight onto the row when it has no readable state."""
        error_message = outcome.errors[0] if outcome.errors else None
        store = RunStateStore(self.db, self.paths, session_id)
        loaded = store.load()
        if not loaded.ok or loaded.state is None:
            if loaded.status != RunStateLoadStatus.MISSING_STATE and loaded.errors:
                warnings.append(loaded.errors[0])
            self.db.update_status(
                session_id,
                outcome.status,
                error_message=error_message,
                worktree_id=outcome.worktree_id,
                worktree_kept=outcome.worktree_kept,
            )
            return

        saved = store.save(
            loaded.state,
            run_status=outcome.status,
            error_message=error_message,
            worktree_id=outcome.worktree_id,
            worktree_kept=outcome.worktree_kept,
        )
        warnings.extend([*loaded.warnings, *saved.warnings])
        if not saved.ok:
            warnings.append(saved.errors[0])

    def _finish_run(self, session_id: str, outcome: RunOutcome, warnings: list[str]) -> None:
        """Finalize the row under the workspace lock, recording persistence failures as warnings."""
        with WorkspaceLock(self.paths.lock_file):
            try:
                self._finalize_row(session_id, outcome, warnings)
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
            use_worktree=config.use_worktree,
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

    def _mark_running(self, session_id: str, warnings: list[str]) -> None:
        """Set the paused row back to running, or record a persistence warning; the caller holds the workspace lock."""
        try:
            self.db.update_status(session_id, RunStatus.RUNNING, pid=os.getpid())
        except Exception as exc:
            warnings.append(f"Failed to update run status in database: {exc}")
