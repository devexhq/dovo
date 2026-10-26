"""Process handle: persist a session row and execute a Blueprint through RunCoordinator."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence

from dovo.common.clock import Clock
from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.filesystem import WorkspacePaths
from dovo.common.lock import WorkspaceLock
from dovo.common.session_id import SessionIdFactory
from dovo.core.agents.models import AgentEnvOverrides
from dovo.core.catalog import Catalog
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.exceptions import GitError
from dovo.core.git.runner import GitRunner
from dovo.core.inputs import InputResolveResult
from dovo.core.interaction import HeadlessAnswerInput
from dovo.core.sessions import current_process_started_at
from dovo.engine.exceptions import EngineInputError
from dovo.engine.executors.failure_log import logged_failure
from dovo.engine.loader import EngineLoader
from dovo.engine.models import (
    DefinitionsManifest,
    FailurePrompter,
    InteractionPrompter,
    RunObserver,
    RunOutcome,
    RunRequest,
    RunStartConfig,
)
from dovo.engine.preset_prompter import PresetInteractionPrompter
from dovo.engine.session import drive_run
from dovo.engine.state_models import SessionStateLoadStatus
from dovo.engine.state_store import SessionStateStore
from dovo.engine.writer import get_session_dir, snapshot_definitions


class Engine:
    """Process: DB row, execution state, and the RunCoordinator-driven step loop."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: SessionsRepository,
        catalog: Catalog,
        *,
        git: GitRunner,
        clock: Clock,
        new_id: SessionIdFactory,
    ) -> None:
        self.paths = paths
        self.path = paths.root_dir
        self.db = db
        self.catalog = catalog
        self.git = git
        self.clock = clock
        self.new_id = new_id

    def run(self, blueprint: Blueprint, request: RunRequest | None = None) -> RunOutcome:
        """Persist the session row and initial state for ``blueprint``, then execute it through ``drive_run``."""
        req = request or RunRequest()
        resolved = self._resolve_run_inputs(blueprint, req)
        sid = req.session_id or self.new_id("blueprint")
        engine_warnings: list[str] = list(resolved.warnings)
        snapshot_warnings: list[str] = []
        snapshot_diagnostics: list[DiagnosticRecord] = []
        manifest = self._snapshot_definitions(blueprint, sid, snapshot_warnings, snapshot_diagnostics)
        if manifest is None:
            return self._failed_outcome(sid, snapshot_warnings, engine_warnings, diagnostics=snapshot_diagnostics)

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
            git=self.git,
            clock=self.clock,
            new_id=self.new_id,
            observer=req.observer,
            prompter=req.failure_prompter,
            interaction_prompter=req.interaction_prompter,
            no_tty=req.no_tty,
            env_overrides=req.env_overrides,
        )
        self._finish_run(sid, outcome, engine_warnings)

        return self._finalize_outcome(outcome, sid, engine_warnings)

    def resume(
        self,
        session_id: str,
        *,
        observer: RunObserver | None = None,
        failure_prompter: FailurePrompter | None = None,
        interaction_prompter: InteractionPrompter | None = None,
        answers: Sequence[HeadlessAnswerInput] | None = None,
        no_tty: bool = False,
        env_overrides: AgentEnvOverrides | None = None,
    ) -> RunOutcome:
        """Load a paused run's durable state and execute it through the same RunCoordinator entry point as run()."""
        engine_warnings: list[str] = []
        with WorkspaceLock(self.paths.lock_file):
            _, state, _ = EngineLoader.load_for_resume(self.db, self.paths, session_id)
            if answers:
                preset = EngineLoader.resolve_answers(session_id, state, answers, engine_warnings)
                interaction_prompter = PresetInteractionPrompter(preset, interaction_prompter)
            self._mark_running(session_id, engine_warnings)

        outcome = drive_run(
            self.paths,
            self.db,
            session_id,
            git=self.git,
            clock=self.clock,
            new_id=self.new_id,
            observer=observer,
            prompter=failure_prompter,
            interaction_prompter=interaction_prompter,
            no_tty=no_tty,
            env_overrides=env_overrides,
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
            context = {"session_id": session_id}
            try:
                self._insert_running(blueprint, session_id, config)
            except Exception as exc:
                return logged_failure(
                    f"Failed to record run start in database: {exc}", exc, operation="engine.persist", context=context
                )

            try:
                initialized = SessionStateStore(self.db, self.paths, session_id).initialize(blueprint, manifest)
            except Exception as exc:
                message = logged_failure(
                    f"Failed to initialize run state: {exc}", exc, operation="engine.persist", context=context
                )
            else:
                if initialized.ok:
                    return None
                message = initialized.errors[0] if initialized.errors else "Failed to initialize run state."

            try:
                self.db.update_status(session_id, SessionStatus.FAILED, error_message=message)
            except Exception as exc:
                message = logged_failure(message, exc, operation="engine.persist", context=context)
            return message

    def _failed_outcome(
        self,
        session_id: str,
        errors: list[str],
        warnings: list[str],
        *,
        diagnostics: Sequence[DiagnosticRecord] = (),
    ) -> RunOutcome:
        """Build the FAILED RunOutcome for a run that could not start, carrying any recorded diagnostics."""
        return RunOutcome(
            status=SessionStatus.FAILED,
            errors=errors,
            warnings=warnings,
            diagnostics=list(diagnostics),
            worktree_path=self.path,
            session_id=session_id,
        )

    def _snapshot_definitions(
        self,
        blueprint: Blueprint,
        session_id: str,
        warnings: list[str],
        diagnostics: list[DiagnosticRecord],
    ) -> DefinitionsManifest | None:
        """Snapshot the resolved blueprint and its uses: step references into the session directory."""
        session_dir = get_session_dir(self.paths, session_id)
        return snapshot_definitions(self.catalog, blueprint, session_dir, warnings, diagnostics)

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
            discard=request.discard,
            agent=request.agent,
            inputs=dict(resolved_values),
            auto_apply=request.auto_apply,
        )

    def _head_commit_sha(self) -> str | None:
        """Return the repository HEAD commit SHA, or None when it cannot be resolved."""
        try:
            return self.git.rev_parse(self.path)
        except GitError:
            return None

    def _finalize_row(self, session_id: str, outcome: RunOutcome, warnings: list[str]) -> None:
        """Persist the outcome through the state store, or straight onto the row when it has no readable state."""
        error_message = outcome.errors[0] if outcome.errors else None
        store = SessionStateStore(self.db, self.paths, session_id)
        loaded = store.load()
        if not loaded.ok or loaded.state is None:
            if loaded.status != SessionStateLoadStatus.MISSING_STATE and loaded.errors:
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
                warnings.append(_status_write_failure(exc, session_id))

    def _insert_running(self, blueprint: Blueprint, session_id: str, config: RunStartConfig) -> None:
        """Insert a RUNNING row carrying the resolved run configuration."""
        self.db.create(
            session_id=session_id,
            blueprint_name=blueprint.name,
            blueprint_key=blueprint.key,
            branch_name="",
            status=SessionStatus.RUNNING,
            pid=os.getpid(),
            pid_started_at=current_process_started_at(),
            blueprint_tier=config.blueprint_tier,
            commit_sha=config.commit_sha,
            use_worktree=config.use_worktree,
            keep=config.keep,
            discard=config.discard,
            agent=config.agent,
            inputs_json=json.dumps(config.inputs, sort_keys=True),
            auto_apply=config.auto_apply,
        )

    def _resolve_run_inputs(self, blueprint: Blueprint, request: RunRequest) -> InputResolveResult:
        """Apply defaults and required checks; raise before a session row is inserted."""
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
            self.db.update_status(
                session_id, SessionStatus.RUNNING, pid=os.getpid(), pid_started_at=current_process_started_at()
            )
        except Exception as exc:
            warnings.append(_status_write_failure(exc, session_id))


def _status_write_failure(exc: Exception, session_id: str) -> str:
    """Record a failed run status write and return its warning with the 'Details logged' line."""
    return logged_failure(
        f"Failed to update run status in database: {exc}",
        exc,
        operation="engine.persist",
        context={"session_id": session_id},
    )
