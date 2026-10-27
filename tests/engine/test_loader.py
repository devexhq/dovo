"""Contract tests for EngineLoader: paused-run validation before coordinator dispatch."""

from __future__ import annotations

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionRecord, SessionsRepository, SessionStatus
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.interaction import HeadlessAnswerInput
from dovo.engine import EngineError, EngineLoader, SessionStateStore
from dovo.engine.executors.models import StepResult
from dovo.engine.models import LifecycleUpdate
from dovo.engine.projection import build_session_json_payload
from dovo.engine.state_models import (
    ExecutionLeafNode,
    ExecutionLoopNode,
    NodeState,
    StepAttemptRecord,
)
from dovo.engine.writer import snapshot_blueprint_path
from tests.harness.sessions import (
    SEEDED_FAILURE,
    SEEDED_SPAN,
    seed_new_session,
    seed_paused_ask_session,
    seed_paused_session,
)

_STEPS: list[dict[str, object]] = [{"id": "a", "run": "true"}, {"id": "b", "run": "exit 1"}]
_LOOP_STEPS: list[dict[str, object]] = [
    {
        "id": "fix",
        "type": "loop",
        "max_iterations": 2,
        "until": ["iteration.index >= 2"],
        "do": [{"id": "edit", "run": "true"}, {"id": "verify", "run": "exit 1"}],
    }
]


def _seed(
    paths: WorkspacePaths,
    sessions: SessionsRepository,
    *,
    use_worktree: bool = False,
    worktree_id: str | None = None,
) -> SessionRecord:
    return seed_paused_session(
        paths,
        sessions,
        session_id="paused-1",
        steps=_STEPS,
        paused_step_id="b",
        use_worktree=use_worktree,
        worktree_id=worktree_id,
    )


def _break_state(paths: WorkspacePaths, sessions: SessionsRepository, fault: str) -> None:
    """Corrupt the seeded paused run's state document in the way fault names."""
    store = SessionStateStore(sessions, paths, "paused-1")
    state = store.load([])
    if fault == "paused-leaf-without-result":
        paused = state.nodes[1]
        assert isinstance(paused, ExecutionLeafNode)
        paused.attempts[-1] = paused.attempts[-1].model_copy(update={"result": None})
    else:
        for node in state.nodes:
            node.state = NodeState.COMPLETED
    store.save(state, [])


def _break_seeded_run(paths: WorkspacePaths, sessions: SessionsRepository, fault: str) -> None:
    """Seed the paused run, then break its row, state, projection or snapshot in the way fault names."""
    row = _seed(paths, sessions)
    snapshot = snapshot_blueprint_path(paths.session_dir("paused-1"), "paused-1")
    if fault == "completed-row":
        sessions.update_status("paused-1", SessionStatus.COMPLETED)
    elif fault == "corrupt-json":
        sessions.save_execution_state(
            "paused-1", "{not json", expected_revision=row.execution_state_revision or 0, next_revision=99
        )
    elif fault == "newer-projection":
        state = SessionStateStore(sessions, paths, "paused-1").load([])
        newer = build_session_json_payload(state.model_copy(update={"revision": state.revision + 1}), row)
        (paths.session_dir("paused-1") / "session.json").write_text(newer.model_dump_json())
    elif fault == "deleted-snapshot":
        snapshot.unlink()
    elif fault == "invalid-snapshot-yaml":
        snapshot.write_text("- a\n- list\n")
    else:
        _break_state(paths, sessions, fault)


def _seed_paused_loop(paths: WorkspacePaths, sessions: SessionsRepository) -> SessionStateStore:
    """Seed a paused run: loop `fix` has completed step `edit` and a PAUSED `verify` on a failed attempt."""
    seed_new_session(paths, sessions, session_id="paused-1", steps=_LOOP_STEPS)
    row = sessions.get("paused-1")
    assert row is not None
    sessions.update_status("paused-1", SessionStatus.PAUSED)
    store = SessionStateStore(sessions, paths, "paused-1")
    state = store.load([])
    loop = state.nodes[0]
    assert isinstance(loop, ExecutionLoopNode)

    def attempt(step_id: str, status: str) -> StepAttemptRecord:
        result = StepResult(
            step_id=step_id,
            status=status,
            exit_code=0 if status == "completed" else 1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=None if status == "completed" else SEEDED_FAILURE,
        )
        return StepAttemptRecord(number=1, span=SEEDED_SPAN, result=result)

    loop.state = NodeState.RUNNING
    loop.iterations[0].state = NodeState.RUNNING
    edit, verify = loop.iterations[0].steps
    edit.attempts = [attempt("edit", "completed")]
    edit.state = NodeState.COMPLETED
    verify.attempts = [attempt("verify", "failed")]
    verify.state = NodeState.PAUSED
    store.save(state, [], LifecycleUpdate(run_status=SessionStatus.PAUSED))
    return store


class EngineLoaderTests:
    def test_paused_run_returns_row_state_and_manifest(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo)

        row, state, manifest = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert row.session_id == "paused-1"
        assert manifest == state.manifest
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.PAUSED]

    @pytest.mark.parametrize(
        ("fault", "code", "message"),
        [
            pytest.param("no-row", ErrorCode.SESSION_NOT_FOUND, "Session 'paused-1' not found.", id="not-found"),
            pytest.param(
                "completed-row",
                ErrorCode.SESSION_NOT_RESUMABLE,
                "Cannot resume session 'paused-1': status is 'completed' (expected paused).",
                id="not-resumable",
            ),
            pytest.param(
                "no-state",
                ErrorCode.SESSION_STATE_MISSING,
                "Cannot resume session 'paused-1': Session 'paused-1' has no execution state.",
                id="missing-state",
            ),
            pytest.param(
                "corrupt-json",
                ErrorCode.SESSION_STATE_CORRUPT,
                "Cannot resume session 'paused-1': Execution state for session 'paused-1' is corrupt.",
                id="corrupt-state",
            ),
            pytest.param(
                "newer-projection",
                ErrorCode.SESSION_STATE_INCONSISTENT,
                "Cannot resume session 'paused-1': session.json for session 'paused-1' is at revision 2, "
                "newer than the database revision 1.",
                id="inconsistent-state",
            ),
            pytest.param(
                "deleted-snapshot",
                ErrorCode.SESSION_SNAPSHOT_MISSING,
                "Cannot resume session 'paused-1': Definition snapshot file missing: {snapshot}",
                id="missing-snapshot",
            ),
            pytest.param(
                "invalid-snapshot-yaml",
                ErrorCode.SESSION_SNAPSHOT_INVALID,
                "Cannot resume session 'paused-1': ",
                id="invalid-snapshot",
            ),
            pytest.param(
                "paused-leaf-without-result",
                ErrorCode.SESSION_STATE_CORRUPT,
                "Cannot resume session 'paused-1': paused step 'b' has no recorded attempt result.",
                id="paused-leaf-no-result",
            ),
            pytest.param(
                "all-nodes-terminal",
                ErrorCode.SESSION_STATE_CORRUPT,
                "Cannot resume session 'paused-1': execution state has no step left to run.",
                id="nothing-runnable",
            ),
        ],
    )
    def test_invalid_run_raises_engine_resume_error_with_code_and_message(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        fault: str,
        code: ErrorCode,
        message: str,
    ) -> None:
        """Raise EngineError whose error_code is the Resume failure code and whose message is today's text."""
        snapshot = snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1")
        if fault == "no-state":
            sessions_repo.create("paused-1", blueprint_name="x", blueprint_key="x", status=SessionStatus.PAUSED)
        elif fault != "no-row":
            _break_seeded_run(engine_paths, sessions_repo, fault)

        with pytest.raises(EngineError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.error_code is code
        if fault == "invalid-snapshot-yaml":
            assert exc_info.value.message.startswith(message)
        else:
            assert exc_info.value.message == message.format(snapshot=snapshot)

    def test_worktree_run_with_existing_directory_loads(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo, use_worktree=True, worktree_id="dovo_1")
        engine_paths.worktree_dir("dovo_1").mkdir(parents=True)

        row, _, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert row.worktree_id == "dovo_1"

    @pytest.mark.parametrize(
        "worktree_id", [pytest.param(None, id="no-id"), pytest.param("gone", id="deleted-directory")]
    )
    def test_worktree_run_without_retained_directory_raises_worktree_missing_and_keeps_row(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, worktree_id: str | None
    ) -> None:
        _seed(engine_paths, sessions_repo, use_worktree=True, worktree_id=worktree_id)

        with pytest.raises(EngineError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.error_code is ErrorCode.SESSION_WORKTREE_MISSING
        assert "no longer exists" in str(exc_info.value)
        assert sessions_repo.get("paused-1") is not None


class EngineLoaderPausedBodyLeafTests:
    def test_paused_body_leaf_with_recorded_result_loads(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed_paused_loop(engine_paths, sessions_repo)

        _, state, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        assert [leaf.state for leaf in loop.iterations[0].steps] == [NodeState.COMPLETED, NodeState.PAUSED]

    def test_paused_body_leaf_without_recorded_result_is_corrupt_state(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        store = _seed_paused_loop(engine_paths, sessions_repo)
        state = store.load([])
        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        paused = loop.iterations[0].steps[1]
        paused.attempts[-1] = paused.attempts[-1].model_copy(update={"result": None})
        store.save(state, [])

        with pytest.raises(EngineError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.error_code is ErrorCode.SESSION_STATE_CORRUPT
        assert "'verify'" in str(exc_info.value)


class EngineLoaderCorruptLoopStateTests:
    def test_structurally_corrupt_loop_state_is_corrupt_state_on_resume(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """Raise SESSION_STATE_CORRUPT with the validator's text when loop iteration children differ from the snapshot body."""
        store = _seed_paused_loop(engine_paths, sessions_repo)
        state = store.load([])
        loop = state.nodes[0]
        assert isinstance(loop, ExecutionLoopNode)
        loop.iterations[0].steps.reverse()
        store.save(state, [])

        with pytest.raises(EngineError) as exc_info:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert exc_info.value.error_code is ErrorCode.SESSION_STATE_CORRUPT
        assert str(exc_info.value).endswith(
            "Loop 'fix' iteration 1 steps ['verify', 'edit'] do not match the loop body ['edit', 'verify']."
        )


_ASK_STEPS: list[dict[str, object]] = [
    {
        "id": "approve",
        "type": "ask",
        "interaction": {
            "mode": "select",
            "message": "Approve?",
            "options": [
                {"value": "approve", "label": "Approve", "comment": "disabled"},
                {"value": "revise", "label": "Revise", "comment": "required"},
                {"value": "deny", "label": "Deny", "fail": True},
            ],
        },
    }
]


class EngineLoaderPausedAskTests:
    def test_paused_ask_leaf_without_attempt_result_is_resumable(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        seed_paused_ask_session(
            engine_paths, sessions_repo, artifacts_repository, session_id="paused-1", steps=_ASK_STEPS
        )

        row, state, manifest = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        leaf = state.nodes[0]
        assert isinstance(leaf, ExecutionLeafNode)
        assert (row.session_id, manifest) == ("paused-1", state.manifest)
        assert (leaf.pending_ask is not None, leaf.attempts[-1].result) == (True, None)

    def test_paused_leaf_without_pending_ask_or_result_is_corrupt(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        seed_paused_ask_session(
            engine_paths, sessions_repo, artifacts_repository, session_id="paused-1", steps=_ASK_STEPS
        )
        store = SessionStateStore(sessions_repo, engine_paths, "paused-1")
        state = store.load([])
        leaf = state.nodes[0]
        assert isinstance(leaf, ExecutionLeafNode)
        leaf.pending_ask = None
        store.save(state, [])

        with pytest.raises(EngineError) as raised:
            EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        assert raised.value.error_code is ErrorCode.SESSION_STATE_CORRUPT


class EngineLoaderResolveAnswersTests:
    def test_valid_input_maps_to_normalized_answer_by_step_id(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        seed_paused_ask_session(
            engine_paths, sessions_repo, artifacts_repository, session_id="paused-1", steps=_ASK_STEPS
        )
        _, state, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")
        warnings: list[str] = []

        answers = EngineLoader.resolve_answers(
            "paused-1", state, [HeadlessAnswerInput(step_id="approve", value="revise", comment="Keep it")], warnings
        )

        assert list(answers) == ["approve"]
        assert (answers["approve"].selected, answers["approve"].comment) == (["revise"], "Keep it")
        assert warnings == []

    @pytest.mark.parametrize(
        ("supplied", "expected_message"),
        [
            pytest.param(
                HeadlessAnswerInput(step_id="other", value="approve"),
                "Prompt 'other' is not pending in this session.\nFix: use one of: approve",
                id="unknown_step",
            ),
            pytest.param(
                HeadlessAnswerInput(step_id="approve", value="maybe"),
                "'maybe' is not a valid answer for prompt 'approve'.\nFix: use one of: approve, revise, deny",
                id="unknown_value",
            ),
            pytest.param(
                HeadlessAnswerInput(step_id="approve", value="revise"),
                "Prompt 'approve' answered 'revise' requires a comment.\nFix: pass --comment or --comment-file.",
                id="missing_required_comment",
            ),
            pytest.param(
                HeadlessAnswerInput(step_id="approve", value="revise", comment="x" * 8001),
                "Comment is too long (max 8000 characters).",
                id="comment_too_long",
            ),
        ],
    )
    def test_rejections_raise_classified_resume_errors(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
        supplied: HeadlessAnswerInput,
        expected_message: str,
    ) -> None:
        seed_paused_ask_session(
            engine_paths, sessions_repo, artifacts_repository, session_id="paused-1", steps=_ASK_STEPS
        )
        _, state, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        with pytest.raises(EngineError) as raised:
            EngineLoader.resolve_answers("paused-1", state, [supplied], [])

        assert raised.value.error_code is ErrorCode.INTERACTION_ANSWER_INVALID
        assert str(raised.value) == expected_message

    def test_state_without_pending_ask_raises_no_pending_prompt(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        _seed(engine_paths, sessions_repo)
        _, state, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")

        with pytest.raises(EngineError) as raised:
            EngineLoader.resolve_answers("paused-1", state, [HeadlessAnswerInput(step_id="b", value="x")], [])

        assert raised.value.error_code is ErrorCode.INTERACTION_PROMPT_NOT_PENDING
        assert str(raised.value) == "No prompt is pending for session 'paused-1'."

    def test_comment_on_disabled_policy_adds_a_warning(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        artifacts_repository: ArtifactsRepository,
    ) -> None:
        seed_paused_ask_session(
            engine_paths, sessions_repo, artifacts_repository, session_id="paused-1", steps=_ASK_STEPS
        )
        _, state, _ = EngineLoader.load_for_resume(sessions_repo, engine_paths, "paused-1")
        warnings: list[str] = []

        answers = EngineLoader.resolve_answers(
            "paused-1", state, [HeadlessAnswerInput(step_id="approve", value="approve", comment="hi")], warnings
        )

        assert answers["approve"].comment is None
        assert len(warnings) == 1
