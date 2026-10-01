"""Run state store: canonical execution state on the run row, with a run.json projection."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from worktree.common.filesystem import WorkspacePaths
from worktree.core.blueprint import Blueprint
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.models import DefinitionsManifest
from worktree.core.engine.projection import build_run_json_payload
from worktree.core.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    RunJsonPayload,
    RunStateLoadResult,
    RunStateLoadStatus,
    RunStateWriteResult,
    RunStateWriteStatus,
)
from worktree.core.engine.writer import snapshot_blueprint_path, snapshot_step_path, write_session_run_projection
from worktree.core.step import LoopStepBlock, StepDefinition


def _leaf_node(step: StepDefinition) -> ExecutionLeafNode:
    """Build a PENDING leaf node carrying the step's id and name."""
    return ExecutionLeafNode(id=step.id, name=step.name)


def new_iteration(loop: LoopStepBlock, number: int) -> ExecutionIterationRecord:
    """Build a PENDING iteration numbered number holding a PENDING leaf per loop.do step, in order."""
    return ExecutionIterationRecord(number=number, steps=[_leaf_node(sub_step) for sub_step in loop.do])


def _loop_node(loop: LoopStepBlock) -> ExecutionLoopNode:
    """Build a PENDING loop node with its frozen config and PENDING iteration 1."""
    return ExecutionLoopNode(
        id=loop.id,
        max_iterations=loop.max_iterations,
        until=list(loop.until),
        on_max_iterations=loop.on_max_iterations,
        iterations=[new_iteration(loop, 1)],
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


def _projection_stale(projected: RunJsonPayload | None, state: ExecutionStateTree, row: RunRecord) -> bool:
    """Return True when run.json is absent or differs from the projection of state and its run row."""
    return projected != build_run_json_payload(state, row)


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
            warnings=self._sync_projection(state, row),
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

        projected = self._read_projection()
        if projected is not None and projected.revision > state.revision:
            return RunStateLoadResult(
                status=RunStateLoadStatus.INCONSISTENT_PROJECTION,
                errors=[
                    f"run.json for run '{self.session_id}' is at revision {projected.revision}, "
                    f"newer than the database revision {state.revision}."
                ],
            )

        warnings = self._sync_projection(state, row) if _projection_stale(projected, state, row) else []
        return RunStateLoadResult(status=RunStateLoadStatus.OK, state=state, warnings=warnings)

    def save(
        self,
        state: ExecutionStateTree,
        run_status: RunStatus | None = None,
        error_message: str | None = None,
        sandbox_id: str | None = None,
        sandbox_kept: bool | None = None,
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
            sandbox_kept=sandbox_kept,
        )
        if row is None:
            return self._classify_write_failure()

        return RunStateWriteResult(
            status=RunStateWriteStatus.OK,
            state=next_state,
            warnings=self._sync_projection(next_state, row),
        )

    def regenerate_projection(self, state: ExecutionStateTree, row: RunRecord) -> Path:
        """Atomically write the run.json projection of state and its run row and return that path."""
        payload = build_run_json_payload(state, row)
        return write_session_run_projection(self.paths.session_dir(self.session_id), payload)

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

    def _sync_projection(self, state: ExecutionStateTree, row: RunRecord) -> list[str]:
        """Write the run.json projection, returning a warning list instead of raising on OSError."""
        try:
            self.regenerate_projection(state, row)
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

    def _read_projection(self) -> RunJsonPayload | None:
        """Return the run.json payload, or None when the file is absent, unreadable, or invalid."""
        target_file = self.paths.session_dir(self.session_id) / "run.json"
        try:
            return RunJsonPayload.model_validate_json(target_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
