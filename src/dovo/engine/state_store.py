"""Run state store: canonical execution state on the session row, with a session.json projection."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.catalog.blueprint import Blueprint
from dovo.core.catalog.definitions import LoopStepBlock, StepDefinition
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.engine.exceptions import EngineError
from dovo.engine.models import DefinitionsManifest
from dovo.engine.projection import build_session_json_payload
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionStateTree,
    SessionJsonPayload,
)
from dovo.engine.writer import snapshot_blueprint_path, snapshot_step_path, write_session_projection


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


def _projection_stale(projected: SessionJsonPayload | None, state: ExecutionStateTree, row: SessionRecord) -> bool:
    """Return True when session.json is absent or differs from the projection of state and its session row."""
    return projected != build_session_json_payload(state, row)


class SessionStateStore:
    """Canonical execution state for one run: the session row owns it and session.json is its projection."""

    def __init__(self, sessions: SessionsRepository, paths: WorkspacePaths, session_id: str) -> None:
        """Bind the store to one run's row and session directory."""
        self.sessions = sessions
        self.paths = paths
        self.session_id = session_id

    def initialize(
        self, blueprint: Blueprint, manifest: DefinitionsManifest, warnings: list[str]
    ) -> ExecutionStateTree:
        """Build the revision 0 tree, commit it to the session row and return it, appending any session.json projection warning.

        Raises:
            EngineError: SESSION_NOT_FOUND or SESSION_STATE_REVISION_CONFLICT, leaving the row and projection untouched.
        """
        state = ExecutionStateTree(manifest=manifest, nodes=list(_plan_nodes(blueprint.steps)))
        row = self.sessions.save_execution_state(
            self.session_id,
            state.model_dump_json(),
            expected_revision=0,
            next_revision=0,
        )
        if row is None:
            raise self._write_failure()

        warnings.extend(self._sync_projection(state, row))
        return state

    def load(self, warnings: list[str]) -> ExecutionStateTree:
        """Return the run's validated state from its row, appending any session.json projection warning to warnings.

        Raises:
            EngineError: SESSION_NOT_FOUND, SESSION_STATE_MISSING, SESSION_STATE_CORRUPT,
                SESSION_SNAPSHOT_MISSING or SESSION_STATE_INCONSISTENT.
        """
        row = self.sessions.get(self.session_id)
        if row is None:
            raise EngineError(ErrorCode.SESSION_NOT_FOUND, f"Session '{self.session_id}' not found.")

        if row.execution_state_json is None:
            raise EngineError(ErrorCode.SESSION_STATE_MISSING, f"Session '{self.session_id}' has no execution state.")

        state = self._parse_state(row)
        if state is None:
            raise EngineError(
                ErrorCode.SESSION_STATE_CORRUPT, f"Execution state for session '{self.session_id}' is corrupt."
            )

        missing = _missing_snapshot_paths(self.paths.session_dir(self.session_id), state.manifest)
        if missing:
            raise EngineError(ErrorCode.SESSION_SNAPSHOT_MISSING, f"Definition snapshot file missing: {missing[0]}")

        projected = self._read_projection()
        if projected is not None and projected.revision > state.revision:
            raise EngineError(
                ErrorCode.SESSION_STATE_INCONSISTENT,
                f"session.json for session '{self.session_id}' is at revision {projected.revision}, "
                f"newer than the database revision {state.revision}.",
            )

        if _projection_stale(projected, state, row):
            warnings.extend(self._sync_projection(state, row))
        return state

    def save(
        self,
        state: ExecutionStateTree,
        warnings: list[str],
        run_status: SessionStatus | None = None,
        error_message: str | None = None,
        worktree_id: str | None = None,
        worktree_kept: bool | None = None,
    ) -> ExecutionStateTree:
        """Commit state at revision + 1 with optional lifecycle fields and return it, appending any session.json projection warning.

        Raises:
            EngineError: SESSION_NOT_FOUND or SESSION_STATE_REVISION_CONFLICT, leaving the row and projection untouched.
        """
        next_state = state.model_copy(update={"revision": state.revision + 1})
        row = self.sessions.save_execution_state(
            self.session_id,
            next_state.model_dump_json(),
            expected_revision=state.revision,
            next_revision=next_state.revision,
            status=run_status,
            error_message=error_message,
            worktree_id=worktree_id,
            worktree_kept=worktree_kept,
        )
        if row is None:
            raise self._write_failure()

        warnings.extend(self._sync_projection(next_state, row))
        return next_state

    def regenerate_projection(self, state: ExecutionStateTree, row: SessionRecord) -> Path:
        """Atomically write the session.json projection of state and its session row and return that path."""
        payload = build_session_json_payload(state, row)
        return write_session_projection(self.paths.session_dir(self.session_id), payload)

    def _write_failure(self) -> EngineError:
        """Return SESSION_NOT_FOUND when the session row is missing, else SESSION_STATE_REVISION_CONFLICT."""
        if self.sessions.get(self.session_id) is None:
            return EngineError(ErrorCode.SESSION_NOT_FOUND, f"Session '{self.session_id}' not found.")

        return EngineError(
            ErrorCode.SESSION_STATE_REVISION_CONFLICT,
            f"Execution state for session '{self.session_id}' was changed by another writer.",
        )

    def _sync_projection(self, state: ExecutionStateTree, row: SessionRecord) -> list[str]:
        """Write the session.json projection, returning a warning list instead of raising on OSError."""
        try:
            self.regenerate_projection(state, row)
        except OSError as exc:
            return [f"Failed to write session.json projection: {exc}; it will be regenerated on the next load."]
        return []

    def _parse_state(self, row: SessionRecord) -> ExecutionStateTree | None:
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

    def _read_projection(self) -> SessionJsonPayload | None:
        """Return the session.json payload, or None when the file is absent, unreadable, or invalid."""
        target_file = self.paths.session_dir(self.session_id) / "session.json"
        try:
            return SessionJsonPayload.model_validate_json(target_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
