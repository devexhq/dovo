"""Engine loader: validate a paused run's row, execution state, snapshots, and retained worktree before dispatch."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.catalog.exceptions import BlueprintLoadError, BlueprintValidationError
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.interaction import HeadlessAnswerInput, Interaction, InteractionAnswer
from dovo.engine.exceptions import EngineError, EngineResumeError, EngineSnapshotMissingError
from dovo.engine.models import DefinitionsManifest
from dovo.engine.projection import iter_leaves
from dovo.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionStateTree,
    NodeState,
)
from dovo.engine.state_store import SessionStateStore
from dovo.engine.state_validation import validate_loop_structure
from dovo.engine.writer import get_session_dir, load_blueprint_from_snapshot


class EngineLoader:
    """Validates a paused run's row, execution state, snapshots, and retained worktree before dispatch."""

    @classmethod
    def load_for_resume(
        cls,
        sessions: SessionsRepository,
        paths: WorkspacePaths,
        session_id: str,
    ) -> tuple[SessionRecord, ExecutionStateTree, DefinitionsManifest]:
        """Return the paused run's row, state tree, and manifest, or raise EngineResumeError with the failure's ErrorCode."""
        row = sessions.get(session_id)
        if row is None:
            raise EngineResumeError(ErrorCode.SESSION_NOT_FOUND, f"Session '{session_id}' not found.")

        if row.status != SessionStatus.PAUSED:
            raise EngineResumeError(
                ErrorCode.SESSION_NOT_RESUMABLE,
                f"Cannot resume session '{session_id}': status is '{row.status.value}' (expected paused).",
            )

        try:
            # The coordinator reloads the state at run start and reports any projection warning itself.
            state = SessionStateStore(sessions, paths, session_id).load([])
        except EngineError as error:
            raise EngineResumeError(
                error.error_code, f"Cannot resume session '{session_id}': {error.message}"
            ) from error

        cls._validate_paused_leaves(session_id, state)
        cls._check_retained_worktree(session_id, paths, row)
        cls._check_definitions(session_id, paths, state)
        return row, state, state.manifest

    @classmethod
    def resolve_answers(
        cls,
        session_id: str,
        state: ExecutionStateTree,
        supplied: Sequence[HeadlessAnswerInput],
        warnings: list[str],
    ) -> dict[str, InteractionAnswer]:
        """Validate supplied answers against the single pending ask; raise EngineResumeError(INTERACTION_NO_PENDING_PROMPT | INTERACTION_ANSWER_INVALID)."""
        pending = next(
            (leaf.pending_ask for leaf in iter_leaves(state) if leaf.state is NodeState.PAUSED and leaf.pending_ask),
            None,
        )
        if pending is None:
            raise EngineResumeError(
                ErrorCode.INTERACTION_NO_PENDING_PROMPT, f"No prompt is pending for session '{session_id}'."
            )

        answers: dict[str, InteractionAnswer] = {}
        for item in supplied:
            parsed = Interaction.parse_headless(pending, item)
            if parsed.answer is None:
                raise EngineResumeError(ErrorCode.INTERACTION_ANSWER_INVALID, parsed.errors[0])
            warnings.extend(parsed.warnings)
            answers[pending.step_id] = parsed.answer
        return answers

    @classmethod
    def _validate_paused_leaves(cls, session_id: str, state: ExecutionStateTree) -> None:
        """Raise SESSION_STATE_CORRUPT unless a non-terminal node exists and every PAUSED leaf has a pending_ask or a recorded last-attempt result."""
        if all(node.state in TERMINAL_NODE_STATES for node in state.nodes):
            raise EngineResumeError(
                ErrorCode.SESSION_STATE_CORRUPT,
                f"Cannot resume session '{session_id}': execution state has no step left to run.",
            )

        for leaf in iter_leaves(state):
            has_result = bool(leaf.attempts) and leaf.attempts[-1].result is not None
            if leaf.state is NodeState.PAUSED and leaf.pending_ask is None and not has_result:
                raise EngineResumeError(
                    ErrorCode.SESSION_STATE_CORRUPT,
                    f"Cannot resume session '{session_id}': paused step '{leaf.id}' has no recorded attempt result.",
                )

    @classmethod
    def _check_retained_worktree(cls, session_id: str, paths: WorkspacePaths, row: SessionRecord) -> None:
        """Raise WORKTREE_MISSING when a worktree-backed run has no recorded worktree_id or its directory no longer exists."""
        if not row.use_worktree:
            return

        worktree_path = paths.worktree_dir(row.worktree_id) if row.worktree_id is not None else None
        if worktree_path is not None and worktree_path.exists():
            return

        raise EngineResumeError(
            ErrorCode.WORKTREE_MISSING,
            f"Cannot resume session '{session_id}': worktree path '{worktree_path or '<none recorded>'}' no longer exists.",
        )

    @classmethod
    def _check_definitions(cls, session_id: str, paths: WorkspacePaths, state: ExecutionStateTree) -> None:
        """Raise SESSION_SNAPSHOT_MISSING or SESSION_SNAPSHOT_INVALID when the snapshot blueprint cannot be rebuilt, SESSION_STATE_CORRUPT when a loop's persisted structure differs from it."""
        try:
            blueprint = load_blueprint_from_snapshot(get_session_dir(paths, session_id), state.manifest)
        except EngineSnapshotMissingError as exc:
            raise EngineResumeError(
                ErrorCode.SESSION_SNAPSHOT_MISSING,
                f"Cannot resume session '{session_id}': {exc}",
            ) from exc
        except (BlueprintLoadError, BlueprintValidationError) as exc:
            raise EngineResumeError(
                ErrorCode.SESSION_SNAPSHOT_INVALID,
                f"Cannot resume session '{session_id}': {exc}",
            ) from exc

        structure_errors = validate_loop_structure(state, blueprint)
        if structure_errors:
            raise EngineResumeError(
                ErrorCode.SESSION_STATE_CORRUPT,
                f"Cannot resume session '{session_id}': execution state is corrupt: {structure_errors[0]}",
            )
