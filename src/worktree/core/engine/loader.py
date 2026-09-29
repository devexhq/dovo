"""Engine loader: validate a paused run's row, execution state, snapshots, and retained sandbox before dispatch."""

from __future__ import annotations

from worktree.common.filesystem import WorkspacePaths
from worktree.core.blueprint.exceptions import (
    BlueprintLoadError,
    BlueprintNotFoundError,
    BlueprintValidationError,
)
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine.exceptions import EngineResumeError, EngineSnapshotMissingError
from worktree.core.engine.models import DefinitionsManifest, EngineResumeStatus
from worktree.core.engine.projection import iter_leaves
from worktree.core.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionStateTree,
    NodeState,
    RunStateLoadStatus,
)
from worktree.core.engine.state_store import RunStateStore
from worktree.core.engine.state_validation import validate_loop_structure
from worktree.core.engine.writer import get_session_dir, load_blueprint_from_snapshot


class EngineLoader:
    """Validates a paused run's row, execution state, snapshots, and retained sandbox before dispatch."""

    @classmethod
    def load_for_resume(
        cls,
        runs: RunsRepository,
        paths: WorkspacePaths,
        session_id: str,
    ) -> tuple[RunRecord, ExecutionStateTree, DefinitionsManifest]:
        """Return the paused run's row, state tree, and manifest, or raise EngineResumeError with the classified status."""
        row = runs.get(session_id)
        if row is None:
            raise EngineResumeError(EngineResumeStatus.NOT_FOUND, f"Session '{session_id}' not found.")

        if row.status != RunStatus.PAUSED:
            raise EngineResumeError(
                EngineResumeStatus.WRONG_STATUS,
                f"Cannot resume session '{session_id}': status is '{row.status.value}' (expected paused).",
            )

        loaded = RunStateStore(runs, paths, session_id).load()
        if not loaded.ok or loaded.state is None:
            raise EngineResumeError(
                cls._state_failure_status(loaded.status),
                f"Cannot resume session '{session_id}': {loaded.errors[0]}",
            )

        cls._validate_paused_leaves(session_id, loaded.state)
        cls._check_retained_sandbox(session_id, paths, row)
        cls._check_definitions(session_id, paths, loaded.state)
        return row, loaded.state, loaded.state.manifest

    @classmethod
    def _state_failure_status(cls, load_status: RunStateLoadStatus) -> EngineResumeStatus:
        """Map a state-load failure to NOT_FOUND, CORRUPT_STATE (missing state), MISSING_SNAPSHOT, or FAILED."""
        if load_status is RunStateLoadStatus.NOT_FOUND:
            return EngineResumeStatus.NOT_FOUND
        if load_status is RunStateLoadStatus.MISSING_STATE:
            return EngineResumeStatus.CORRUPT_STATE
        if load_status is RunStateLoadStatus.MISSING_SNAPSHOT:
            return EngineResumeStatus.MISSING_SNAPSHOT
        return EngineResumeStatus.FAILED

    @classmethod
    def _validate_paused_leaves(cls, session_id: str, state: ExecutionStateTree) -> None:
        """Raise CORRUPT_STATE unless a non-terminal node exists and every PAUSED leaf's last attempt holds a result."""
        if all(node.state in TERMINAL_NODE_STATES for node in state.nodes):
            raise EngineResumeError(
                EngineResumeStatus.CORRUPT_STATE,
                f"Cannot resume session '{session_id}': execution state has no step left to run.",
            )

        for leaf in iter_leaves(state):
            if leaf.state is NodeState.PAUSED and (not leaf.attempts or leaf.attempts[-1].result is None):
                raise EngineResumeError(
                    EngineResumeStatus.CORRUPT_STATE,
                    f"Cannot resume session '{session_id}': paused step '{leaf.id}' has no recorded attempt result.",
                )

    @classmethod
    def _check_retained_sandbox(cls, session_id: str, paths: WorkspacePaths, row: RunRecord) -> None:
        """Raise MISSING_SANDBOX when a sandboxed run has no recorded sandbox_id or its directory no longer exists."""
        if not row.use_sandbox:
            return

        sandbox_path = paths.sandbox_dir(row.sandbox_id) if row.sandbox_id is not None else None
        if sandbox_path is not None and sandbox_path.exists():
            return

        raise EngineResumeError(
            EngineResumeStatus.MISSING_SANDBOX,
            f"Cannot resume session '{session_id}': sandbox path '{sandbox_path or '<none recorded>'}' no longer exists.",
        )

    @classmethod
    def _check_definitions(cls, session_id: str, paths: WorkspacePaths, state: ExecutionStateTree) -> None:
        """Raise MISSING_SNAPSHOT or FAILED when the snapshot blueprint cannot be rebuilt, CORRUPT_STATE when a loop's persisted structure differs from it."""
        try:
            blueprint = load_blueprint_from_snapshot(get_session_dir(paths, session_id), state.manifest)
        except EngineSnapshotMissingError as exc:
            raise EngineResumeError(
                EngineResumeStatus.MISSING_SNAPSHOT,
                f"Cannot resume session '{session_id}': {exc}",
            ) from exc
        except (BlueprintNotFoundError, BlueprintLoadError, BlueprintValidationError) as exc:
            raise EngineResumeError(
                EngineResumeStatus.FAILED,
                f"Cannot resume session '{session_id}': {exc}",
            ) from exc

        structure_errors = validate_loop_structure(state, blueprint)
        if structure_errors:
            raise EngineResumeError(
                EngineResumeStatus.CORRUPT_STATE,
                f"Cannot resume session '{session_id}': execution state is corrupt: {structure_errors[0]}",
            )
