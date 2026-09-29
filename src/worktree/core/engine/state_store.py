"""Run state store: canonical execution state on the run row, with a run.json projection."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from worktree.common.filesystem import Filesystem, WorkspacePaths
from worktree.core.blueprint import Blueprint
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.models import DefinitionsManifest
from worktree.core.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    RunStateLoadResult,
    RunStateLoadStatus,
    RunStateWriteResult,
    RunStateWriteStatus,
)
from worktree.core.engine.writer import snapshot_blueprint_path, snapshot_step_path
from worktree.core.step import LoopStepBlock, StepDefinition


def _leaf_node(step: StepDefinition) -> ExecutionLeafNode:
    """Build a PENDING leaf node carrying the step's id and name."""
    return ExecutionLeafNode(id=step.id, name=step.name)


def _loop_node(loop: LoopStepBlock) -> ExecutionLoopNode:
    """Build a PENDING loop node with its frozen config and one PENDING iteration holding the do steps in order."""
    return ExecutionLoopNode(
        id=loop.id,
        max_iterations=loop.max_iterations,
        until=list(loop.until),
        on_max_iterations=loop.on_max_iterations,
        iterations=[ExecutionIterationRecord(number=1, steps=[_leaf_node(sub_step) for sub_step in loop.do])],
    )


def _plan_nodes(steps: list[StepDefinition | LoopStepBlock]) -> list[ExecutionLeafNode | ExecutionLoopNode]:
    """Build the ordered top-level plan nodes for a blueprint's steps."""
    return [_loop_node(step) if isinstance(step, LoopStepBlock) else _leaf_node(step) for step in steps]


def _missing_snapshot_paths(session_dir: Path, manifest: DefinitionsManifest) -> list[Path]:
    """Return every manifest-referenced snapshot file that is not a readable file."""
    blueprint_key = manifest.blueprint.ref.split(":", 2)[-1]
    paths = [snapshot_blueprint_path(session_dir, blueprint_key)]
    paths.extend(snapshot_step_path(session_dir, ref.ref.split(":", 2)[-1]) for ref in manifest.steps)
    return [path for path in paths if not path.is_file()]


class RunStateStore:
    """Canonical execution state for one run: the run row owns it and run.json is its projection."""

    def __init__(self, runs: RunsRepository, paths: WorkspacePaths, session_id: str) -> None:
        """Bind the store to one run's row and session directory."""
        self.runs = runs
        self.paths = paths
        self.session_id = session_id

    def initialize(self, blueprint: Blueprint, manifest: DefinitionsManifest) -> RunStateWriteResult:
        """Build the revision 0 tree, commit it to the run row, and write the run.json projection."""
        state = ExecutionStateTree(manifest=manifest, nodes=list(_plan_nodes(blueprint.steps)))
        row = self.runs.save_execution_state(
            self.session_id,
            state.model_dump_json(),
            expected_revision=0,
            next_revision=0,
        )
        if row is None:
            return self._classify_write_failure()

        return RunStateWriteResult(
            status=RunStateWriteStatus.OK,
            state=state,
            warnings=self._sync_projection(state),
        )

    def load(self) -> RunStateLoadResult:
        """Load the run's state from its row, validate it and its snapshot files, and reconcile the run.json projection."""
        row = self.runs.get(self.session_id)
        if row is None:
            return RunStateLoadResult(
                status=RunStateLoadStatus.NOT_FOUND,
                errors=[f"Run '{self.session_id}' not found."],
            )

        if row.execution_state_json is None:
            return RunStateLoadResult(
                status=RunStateLoadStatus.MISSING_STATE,
                errors=[f"Run '{self.session_id}' has no execution state."],
            )

        state = self._parse_state(row)
        if state is None:
            return RunStateLoadResult(
                status=RunStateLoadStatus.CORRUPT_STATE,
                errors=[f"Execution state for run '{self.session_id}' is corrupt."],
            )

        missing = _missing_snapshot_paths(self.paths.session_dir(self.session_id), state.manifest)
        if missing:
            return RunStateLoadResult(
                status=RunStateLoadStatus.MISSING_SNAPSHOT,
                errors=[f"Definition snapshot file missing: {path}" for path in missing],
            )

        projection_revision = self._projection_revision()
        if projection_revision is not None and projection_revision > state.revision:
            return RunStateLoadResult(
                status=RunStateLoadStatus.INCONSISTENT_PROJECTION,
                errors=[
                    f"run.json for run '{self.session_id}' is at revision {projection_revision}, "
                    f"newer than the database revision {state.revision}."
                ],
            )

        warnings = self._sync_projection(state) if projection_revision != state.revision else []
        return RunStateLoadResult(status=RunStateLoadStatus.OK, state=state, warnings=warnings)

    def save(
        self,
        state: ExecutionStateTree,
        run_status: RunStatus | None = None,
        error_message: str | None = None,
        sandbox_id: str | None = None,
    ) -> RunStateWriteResult:
        """Commit state at revision + 1 with optional lifecycle fields and sandbox id, then sync the run.json projection."""
        next_state = state.model_copy(update={"revision": state.revision + 1})
        row = self.runs.save_execution_state(
            self.session_id,
            next_state.model_dump_json(),
            expected_revision=state.revision,
            next_revision=next_state.revision,
            status=run_status,
            error_message=error_message,
            sandbox_id=sandbox_id,
        )
        if row is None:
            return self._classify_write_failure()

        return RunStateWriteResult(
            status=RunStateWriteStatus.OK,
            state=next_state,
            warnings=self._sync_projection(next_state),
        )

    def regenerate_projection(self, state: ExecutionStateTree) -> Path:
        """Atomically write state to <session-dir>/run.json and return that path."""
        target_file = self.paths.session_dir(self.session_id) / "run.json"
        Filesystem.atomic_write_text(target_file, state.model_dump_json(indent=2))
        return target_file

    def _classify_write_failure(self) -> RunStateWriteResult:
        """Return NOT_FOUND when the run row is missing, else REVISION_CONFLICT, without touching the row or projection."""
        if self.runs.get(self.session_id) is None:
            return RunStateWriteResult(
                status=RunStateWriteStatus.NOT_FOUND,
                errors=[f"Run '{self.session_id}' not found."],
            )

        return RunStateWriteResult(
            status=RunStateWriteStatus.REVISION_CONFLICT,
            errors=[f"Execution state for run '{self.session_id}' was changed by another writer."],
        )

    def _sync_projection(self, state: ExecutionStateTree) -> list[str]:
        """Write the run.json projection, returning a warning list instead of raising on OSError."""
        try:
            self.regenerate_projection(state)
        except OSError as exc:
            return [f"Failed to write run.json projection: {exc}; it will be regenerated on the next load."]
        return []

    def _parse_state(self, row: RunRecord) -> ExecutionStateTree | None:
        """Return the validated state when the JSON is valid and its revision matches the row, else None."""
        if row.execution_state_json is None:
            return None
        try:
            state = ExecutionStateTree.model_validate_json(row.execution_state_json)
        except ValidationError:
            return None
        if state.revision != row.execution_state_revision:
            return None
        return state

    def _projection_revision(self) -> int | None:
        """Return the run.json revision, or None when the file is absent, unreadable, or invalid."""
        target_file = self.paths.session_dir(self.session_id) / "run.json"
        try:
            return ExecutionStateTree.model_validate_json(target_file.read_text(encoding="utf-8")).revision
        except (OSError, ValueError):
            return None
