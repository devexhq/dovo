"""Paused-session handle: classify a row and expose whether it can resume."""

from __future__ import annotations

from pathlib import Path

from worktree.common.filesystem import WorkspacePaths
from worktree.core.blueprint import Blueprint
from worktree.core.blueprint.exceptions import (
    BlueprintLoadError,
    BlueprintNotFoundError,
    BlueprintValidationError,
)
from worktree.core.catalog import Catalog
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.exceptions import EngineResumeError, EngineSnapshotMissingError
from worktree.core.engine.models import DefinitionsManifest, EngineResumeStatus
from worktree.core.engine.writer import get_session_dir, load_blueprint_from_snapshot, load_session_run
from worktree.core.runtime import RunCheckpoint, parse_checkpoint
from worktree.core.step import LoopStepBlock, StepDefinition


def _collect_blueprint_steps(blueprint: Blueprint) -> tuple[set[str], list[StepDefinition]]:
    """Collect all step IDs and flat list of step definitions from a blueprint."""
    step_ids: set[str] = set()
    steps: list[StepDefinition] = []
    for step in blueprint.steps:
        step_ids.add(step.id)
        if isinstance(step, StepDefinition):
            steps.append(step)
        elif isinstance(step, LoopStepBlock):
            for sub_step in step.do:
                step_ids.add(sub_step.id)
                steps.append(sub_step)
    return step_ids, steps


class ResumableRun:
    """Inspect a stored session. ``load`` never raises; ``is_resumable`` reports readiness."""

    def __init__(
        self,
        session_id: str,
        *,
        paths: WorkspacePaths,
        status: EngineResumeStatus,
        message: str = "",
        checkpoint: RunCheckpoint | None = None,
        steps: list[StepDefinition] | None = None,
        db: RunsRepository | None = None,
        blueprint: Blueprint | None = None,
    ) -> None:
        self.session_id = session_id
        self.paths = paths
        self.path = paths.root_dir
        self.cwd = self.path
        self.status = status
        self.message = message
        self.checkpoint = checkpoint
        self.steps = steps or []
        self.db = db
        self.blueprint = blueprint

    def __str__(self) -> str:
        """Return the classification message for EngineResumeError."""
        return self.message

    @property
    def is_resumable(self) -> bool:
        """Return True when the session is paused and the checkpoint matches the blueprint."""
        return (
            self.status is EngineResumeStatus.OK
            and self.db is not None
            and self.checkpoint is not None
            and self.blueprint is not None
        )

    def ready(self) -> tuple[Blueprint, RunsRepository, RunCheckpoint]:
        """Return blueprint, db, and checkpoint, or raise ``EngineResumeError``."""
        blueprint = self.blueprint
        db = self.db
        checkpoint = self.checkpoint
        if not self.is_resumable or blueprint is None or db is None or checkpoint is None:
            raise EngineResumeError(self.status, str(self))

        return blueprint, db, checkpoint

    @classmethod
    def load(
        cls,
        session_id: str,
        blueprint: Blueprint | None = None,
        *,
        paths: WorkspacePaths,
        db: RunsRepository | None = None,
        catalog: Catalog | None = None,
    ) -> ResumableRun:
        """Classify a session without raising. Check ``is_resumable`` before resuming."""
        runs_db = db if db is not None else RunsRepository(db_path=paths.database_file, project_id=paths.project_id)
        cat = catalog if catalog is not None else Catalog(paths)
        return cls._classify(session_id, blueprint, paths, db=runs_db, catalog=cat)

    @classmethod
    def _classify(
        cls,
        session_id: str,
        blueprint: Blueprint | None,
        paths: WorkspacePaths,
        *,
        db: RunsRepository,
        catalog: Catalog,
    ) -> ResumableRun:
        """Walk row, status, checkpoint, and blueprint checks in order."""
        row = cls._lookup_row(session_id, blueprint, db)
        if row is None:
            return cls._rejected(session_id, paths, EngineResumeStatus.NOT_FOUND, f"Session '{session_id}' not found.")

        if row.status != RunStatus.PAUSED:
            return cls._rejected(
                session_id,
                paths,
                EngineResumeStatus.WRONG_STATUS,
                f"Cannot resume session '{session_id}': status is '{row.status.value}' (expected paused).",
            )

        checkpoint = cls._parse_checkpoint(session_id, row.checkpoint_json, paths)
        if isinstance(checkpoint, ResumableRun):
            return checkpoint

        loaded = blueprint if blueprint is not None else cls._load_blueprint(session_id, row, paths, catalog=catalog)
        if isinstance(loaded, ResumableRun):
            return loaded

        step_ids, steps = _collect_blueprint_steps(loaded)
        if checkpoint.pending_step_id not in step_ids:
            return cls._rejected(
                session_id,
                paths,
                EngineResumeStatus.CORRUPT_CHECKPOINT,
                f"Cannot resume session '{session_id}': checkpoint is missing or corrupt.",
            )

        return cls(
            session_id,
            paths=paths,
            status=EngineResumeStatus.OK,
            checkpoint=checkpoint,
            steps=steps,
            db=db,
            blueprint=loaded,
        )

    @classmethod
    def _rejected(
        cls,
        session_id: str,
        paths: WorkspacePaths,
        status: EngineResumeStatus,
        message: str,
    ) -> ResumableRun:
        """Build a non-resumable handle for a classification failure."""
        return cls(session_id, paths=paths, status=status, message=message)

    @classmethod
    def _lookup_row(
        cls,
        session_id: str,
        blueprint: Blueprint | None,
        db: RunsRepository,
    ) -> RunRecord | None:
        """Return the run row, or None when the session is missing."""
        row = db.get(session_id)
        if row is None:
            return None
        if blueprint is not None and row.blueprint_key != blueprint.key:
            return None
        return row

    @classmethod
    def _parse_checkpoint(cls, session_id: str, raw: str | None, paths: WorkspacePaths) -> RunCheckpoint | ResumableRun:
        """Return a parsed checkpoint, or a rejected handle when it cannot be used."""
        checkpoint = parse_checkpoint(raw)
        if checkpoint is None:
            return cls._rejected(
                session_id,
                paths,
                EngineResumeStatus.CORRUPT_CHECKPOINT,
                f"Cannot resume session '{session_id}': checkpoint is missing or corrupt.",
            )

        if not checkpoint.use_sandbox:
            return checkpoint

        sandbox_path = checkpoint.sandbox_path or ""
        if sandbox_path and Path(sandbox_path).exists():
            return checkpoint

        return cls._rejected(
            session_id,
            paths,
            EngineResumeStatus.MISSING_SANDBOX,
            f"Cannot resume session '{session_id}': sandbox path '{sandbox_path}' no longer exists.",
        )

    @classmethod
    def _load_blueprint(
        cls,
        session_id: str,
        row: RunRecord,
        paths: WorkspacePaths,
        *,
        catalog: Catalog,
    ) -> Blueprint | ResumableRun:
        """Load the session's snapshot blueprint when run.json carries a definitions manifest, else the catalog blueprint."""
        payload = load_session_run(paths, session_id)
        if payload is not None and payload.definitions is not None:
            return cls._load_blueprint_from_snapshot(session_id, paths, payload.definitions)
        return cls._load_blueprint_from_catalog(session_id, row, paths, catalog=catalog)

    @classmethod
    def _load_blueprint_from_catalog(
        cls,
        session_id: str,
        row: RunRecord,
        paths: WorkspacePaths,
        *,
        catalog: Catalog,
    ) -> Blueprint | ResumableRun:
        """Load the catalog blueprint keyed by the paused row."""
        key = row.blueprint_key

        try:
            return Blueprint.load(key, catalog=catalog)
        except (BlueprintNotFoundError, BlueprintLoadError, BlueprintValidationError):
            return cls._rejected(
                session_id,
                paths,
                EngineResumeStatus.FAILED,
                f"Cannot resume session '{session_id}': blueprint '{key}' not found.",
            )

    @classmethod
    def _load_blueprint_from_snapshot(
        cls,
        session_id: str,
        paths: WorkspacePaths,
        manifest: DefinitionsManifest,
    ) -> Blueprint | ResumableRun:
        """Load and resolve the session's own snapshot files, classifying missing-file and validation failures."""
        session_dir = get_session_dir(paths, session_id)
        try:
            return load_blueprint_from_snapshot(session_dir, manifest)
        except EngineSnapshotMissingError as exc:
            return cls._rejected(
                session_id,
                paths,
                EngineResumeStatus.MISSING_SNAPSHOT,
                f"Cannot resume session '{session_id}': {exc}",
            )
        except (BlueprintNotFoundError, BlueprintLoadError, BlueprintValidationError) as exc:
            return cls._rejected(
                session_id,
                paths,
                EngineResumeStatus.FAILED,
                f"Cannot resume session '{session_id}': {exc}",
            )
