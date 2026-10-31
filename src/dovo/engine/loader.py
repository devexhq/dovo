"""Engine loader: validate a paused run's row, execution state, snapshots, and retained worktree before dispatch."""

from __future__ import annotations

from collections.abc import Sequence

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.core.catalog.definitions import BlueprintDefinition
from dovo.core.catalog.exceptions import BlueprintLoadError, BlueprintValidationError
from dovo.core.catalog.models import CatalogItemType
from dovo.core.catalog.services.validate import check_catalog_item
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.interaction import HeadlessAnswerInput, Interaction, InteractionAnswer
from dovo.core.sessions import STALE_RUN_ERROR_MESSAGE
from dovo.engine.exceptions import EngineError
from dovo.engine.models import DefinitionsManifest, LifecycleUpdate
from dovo.engine.projection import iter_leaves
from dovo.engine.state_models import (
    TERMINAL_NODE_STATES,
    ExecutionStateTree,
    NodeState,
)
from dovo.engine.state_store import SessionStateStore
from dovo.engine.state_validation import validate_block_structure
from dovo.engine.writer import get_session_dir, load_blueprint_from_snapshot, snapshot_blueprint_path

_FINISHED_NODE_STATES = frozenset({NodeState.COMPLETED, NodeState.IGNORED, NodeState.SKIPPED})


class EngineLoader:
    """Validates a paused run's row, execution state, snapshots, and retained worktree before dispatch."""

    @classmethod
    def load_for_resume(
        cls,
        sessions: SessionsRepository,
        paths: WorkspacePaths,
        session_id: str,
        validation_warnings: list[str],
    ) -> tuple[SessionRecord, ExecutionStateTree, DefinitionsManifest]:
        """Return the paused run's row, state tree, and manifest, or raise EngineError with the failure's ErrorCode.

        The caller holds the workspace lock. A run whose every top-level node is completed, ignored or skipped loads
        like any other, so the coordinator ends it completed. A run whose every top-level node is terminal with one
        not completed, ignored or skipped has no step left: its row is written failed before SESSION_STATE_CORRUPT is raised.
        The blueprint snapshot is then checked as `dovo blueprint validate` checks a blueprint, each script_path
        under the run's retained worktree (the repository root for an in-place run): its warnings are appended to
        validation_warnings, and its failure propagates as validation raises it, with nothing written.

        Raises:
            CatalogError: CATALOG_FILE_UNREADABLE, CATALOG_SYNTAX_INVALID or CATALOG_SCHEMA_INVALID, carrying every
                error and warning in ``CatalogValidateFindings``, when the snapshot fails validation.
        """
        row = sessions.get(session_id)
        if row is None:
            raise EngineError(ErrorCode.SESSION_NOT_FOUND, f"Session '{session_id}' not found.")

        if row.status != SessionStatus.PAUSED:
            raise EngineError(
                ErrorCode.SESSION_NOT_RESUMABLE,
                f"Cannot resume session '{session_id}': status is '{row.status.value}' (expected paused).",
            )

        try:
            # The coordinator reloads the state at run start and reports any projection warning itself.
            state = SessionStateStore(sessions, paths, session_id).load([])
        except EngineError as error:
            raise EngineError(
                error.error_code, f"Cannot resume session '{session_id}': {error.message}"
            ) from error.__cause__

        if cls._ended_unfinished(state):
            cls._finalize_finished_run(sessions, paths, session_id, state)
            raise EngineError(
                ErrorCode.SESSION_STATE_CORRUPT,
                f"Cannot resume session '{session_id}': execution state has no step left to run.",
            )

        cls._validate_paused_leaves(session_id, state)
        cls._check_retained_worktree(session_id, paths, row)
        validation_warnings.extend(cls._check_snapshot(session_id, paths, row, state.manifest))
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
        """Validate supplied answers against the single pending ask; raise EngineError(INTERACTION_PROMPT_NOT_PENDING | INTERACTION_ANSWER_INVALID)."""
        pending = next(
            (leaf.pending_ask for leaf in iter_leaves(state) if leaf.state is NodeState.PAUSED and leaf.pending_ask),
            None,
        )
        if pending is None:
            raise EngineError(
                ErrorCode.INTERACTION_PROMPT_NOT_PENDING, f"No prompt is pending for session '{session_id}'."
            )

        answers: dict[str, InteractionAnswer] = {}
        for item in supplied:
            parsed = Interaction.parse_headless(pending, item)
            if parsed.answer is None:
                raise EngineError(ErrorCode.INTERACTION_ANSWER_INVALID, parsed.errors[0])
            warnings.extend(parsed.warnings)
            answers[pending.step_id] = parsed.answer
        return answers

    @classmethod
    def _ended_unfinished(cls, state: ExecutionStateTree) -> bool:
        """Return whether every top-level node is terminal and one of them is not completed, ignored or skipped."""
        return all(node.state in TERMINAL_NODE_STATES for node in state.nodes) and any(
            node.state not in _FINISHED_NODE_STATES for node in state.nodes
        )

    @classmethod
    def _finalize_finished_run(
        cls, sessions: SessionsRepository, paths: WorkspacePaths, session_id: str, state: ExecutionStateTree
    ) -> None:
        """Write the row failed with the stale-run message through the state store, so the row and session.json agree."""
        # The loader raises next, so the save's projection warning has no result to reach.
        SessionStateStore(sessions, paths, session_id).save(
            state,
            [],
            LifecycleUpdate(run_status=SessionStatus.FAILED, error_message=STALE_RUN_ERROR_MESSAGE),
        )

    @classmethod
    def _validate_paused_leaves(cls, session_id: str, state: ExecutionStateTree) -> None:
        """Raise SESSION_STATE_CORRUPT when a PAUSED leaf has neither a pending_ask nor a recorded last-attempt result.

        The caller holds the workspace lock and has already settled a run with no step left; a run whose every
        top-level node is completed, ignored or skipped passes.
        """
        for leaf in iter_leaves(state):
            has_result = bool(leaf.attempts) and leaf.attempts[-1].result is not None
            if leaf.state is NodeState.PAUSED and leaf.pending_ask is None and not has_result:
                raise EngineError(
                    ErrorCode.SESSION_STATE_CORRUPT,
                    f"Cannot resume session '{session_id}': paused step '{leaf.id}' has no recorded attempt result.",
                )

    @classmethod
    def _check_retained_worktree(cls, session_id: str, paths: WorkspacePaths, row: SessionRecord) -> None:
        """Raise SESSION_WORKTREE_MISSING when a worktree-backed run has no recorded worktree_id or its directory no longer exists."""
        if not row.use_worktree:
            return

        worktree_path = paths.worktree_dir(row.worktree_id) if row.worktree_id is not None else None
        if worktree_path is not None and worktree_path.exists():
            return

        raise EngineError(
            ErrorCode.SESSION_WORKTREE_MISSING,
            f"Cannot resume session '{session_id}': worktree path '{worktree_path or '<none recorded>'}' no longer exists.",
        )

    @classmethod
    def _check_snapshot(
        cls, session_id: str, paths: WorkspacePaths, row: SessionRecord, manifest: DefinitionsManifest
    ) -> tuple[str, ...]:
        """Check the blueprint snapshot as `dovo blueprint validate` checks a blueprint and return its warnings.

        Raises:
            CatalogError: The validation failure, carrying every error and warning in ``CatalogValidateFindings``.
        """
        _, _, key = manifest.blueprint.ref.split(":", 2)
        worktree_id = row.worktree_id if row.use_worktree else None
        script_root = paths.root_dir if worktree_id is None else paths.worktree_dir(worktree_id)
        checked = check_catalog_item(
            snapshot_blueprint_path(get_session_dir(paths, session_id), key),
            CatalogItemType.BLUEPRINT.value,
            key,
            repo_root=script_root,
            definition_cls=BlueprintDefinition,
        )
        return checked.warnings

    @classmethod
    def _check_definitions(cls, session_id: str, paths: WorkspacePaths, state: ExecutionStateTree) -> None:
        """Raise SESSION_SNAPSHOT_MISSING or SESSION_SNAPSHOT_INVALID when a uses: step snapshot cannot be rebuilt, SESSION_STATE_CORRUPT when a loop's persisted structure differs from the blueprint.

        The blueprint snapshot itself has passed validation by now, so only its uses: step snapshots can fail here.
        """
        try:
            blueprint = load_blueprint_from_snapshot(get_session_dir(paths, session_id), state.manifest)
        except EngineError as error:
            if error.error_code is not ErrorCode.SESSION_SNAPSHOT_MISSING:
                raise
            raise EngineError(
                ErrorCode.SESSION_SNAPSHOT_MISSING,
                f"Cannot resume session '{session_id}': {error}",
            ) from error.__cause__
        except (BlueprintLoadError, BlueprintValidationError) as exc:
            raise EngineError(
                ErrorCode.SESSION_SNAPSHOT_INVALID,
                f"Cannot resume session '{session_id}': {exc}",
            ) from exc

        structure_errors = validate_block_structure(state, blueprint)
        if structure_errors:
            raise EngineError(
                ErrorCode.SESSION_STATE_CORRUPT,
                f"Cannot resume session '{session_id}': execution state is corrupt: {structure_errors[0]}",
            )
