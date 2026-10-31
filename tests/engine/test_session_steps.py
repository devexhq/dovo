"""Contract tests for the read-only session step summaries."""

from __future__ import annotations

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.common.redact import SecretRedactor
from dovo.common.timing import TimeSpan
from dovo.core.db import SessionRecord, SessionsRepository
from dovo.core.sessions import StepRunSummary
from dovo.engine.exceptions import EngineError
from dovo.engine.executors.models import StepResult
from dovo.engine.models import DefinitionRef, DefinitionsManifest, SkipCause
from dovo.engine.session_steps import NO_STEP_DATA_WARNING, summarize_session_steps
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionPlanNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
    StepSkip,
)
from tests.harness.sessions import SEEDED_FAILURE, SEEDED_SPAN, seed_failed_session

_MANIFEST = DefinitionsManifest(
    blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="abc", resolved_at="2026-09-28T00:00:00+00:00")
)
_REDACTOR = SecretRedactor([])


def _attempt(
    number: int, *, exit_code: int, duration_seconds: float, error_message: str | None = None
) -> StepAttemptRecord:
    """Build a finished attempt carrying a StepResult with stdout, stderr and outputs populated."""
    result = StepResult(
        step_id="step",
        status="completed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        stdout="STDOUT-TEXT",
        stderr="STDERR-TEXT",
        duration_seconds=duration_seconds,
        error_message=error_message,
        outputs={"key": "OUTPUT-TEXT"},
    )
    return StepAttemptRecord(number=number, span=SEEDED_SPAN, result=result)


def _record(nodes: list[ExecutionPlanNode]) -> SessionRecord:
    """Build a session row whose execution_state_json holds nodes."""
    tree = ExecutionStateTree(manifest=_MANIFEST, nodes=nodes)
    return SessionRecord(
        project_id="proj-a",
        session_id="s",
        blueprint_name="bp",
        blueprint_key="bp",
        execution_state_json=tree.model_dump_json(),
    )


class SummarizeSessionStepsTests:
    """Contract tests for summarize_session_steps."""

    def test_every_node_state_value_is_reported_verbatim(self) -> None:
        record = _record([ExecutionLeafNode(id=f"step-{state.value}", state=state) for state in NodeState])

        steps = summarize_session_steps(record, _REDACTOR)

        assert [(row.step_id, row.state) for row in steps] == [
            (f"step-{state.value}", state.value) for state in NodeState
        ]

    def test_leaf_with_retries_sums_duration_and_uses_last_attempt(self) -> None:
        leaf = ExecutionLeafNode(
            id="flaky",
            name="Flaky step",
            state=NodeState.COMPLETED,
            attempts=[
                _attempt(1, exit_code=2, duration_seconds=1.5, error_message="first failure"),
                _attempt(2, exit_code=0, duration_seconds=2.0, error_message="recovered with warning"),
            ],
        )

        steps = summarize_session_steps(_record([leaf]), _REDACTOR)

        assert steps == [
            StepRunSummary(
                kind="step",
                step_id="flaky",
                name="Flaky step",
                state="completed",
                attempts=2,
                exit_code=0,
                duration_seconds=3.5,
                error_message="recovered with warning",
            )
        ]

    @pytest.mark.parametrize(
        ("attempts", "expected_attempts"),
        [
            pytest.param([], 0, id="pending-leaf"),
            pytest.param(
                [StepAttemptRecord(number=1, span=TimeSpan(started_at="2026-09-28T00:00:00+00:00"))],
                1,
                id="running-attempt-without-result",
            ),
        ],
    )
    def test_leaf_without_results_has_none_values(
        self, attempts: list[StepAttemptRecord], expected_attempts: int
    ) -> None:
        leaf = ExecutionLeafNode(id="waiting", attempts=attempts)

        steps = summarize_session_steps(_record([leaf]), _REDACTOR)

        assert steps == [StepRunSummary(kind="step", step_id="waiting", state="pending", attempts=expected_attempts)]

    def test_loop_with_multiple_iterations_flattens_in_plan_order(self) -> None:

        def body_leaf(step_id: str) -> ExecutionLeafNode:
            return ExecutionLeafNode(
                id=step_id, state=NodeState.COMPLETED, attempts=[_attempt(1, exit_code=0, duration_seconds=1.0)]
            )

        loop = ExecutionLoopNode(
            id="build-loop",
            max_iterations=3,
            on_max_iterations=FailurePolicy.ABORT,
            state=NodeState.COMPLETED,
            iterations=[
                ExecutionIterationRecord(number=number, steps=[body_leaf("code"), body_leaf("review")])
                for number in (1, 2)
            ],
        )

        steps = summarize_session_steps(_record([ExecutionLeafNode(id="setup"), loop]), _REDACTOR)

        assert [(row.kind, row.step_id, row.loop_id, row.iteration, row.iterations) for row in steps] == [
            ("step", "setup", None, None, None),
            ("loop", "build-loop", None, None, 2),
            ("step", "code", "build-loop", 1, None),
            ("step", "review", "build-loop", 1, None),
            ("step", "code", "build-loop", 2, None),
            ("step", "review", "build-loop", 2, None),
        ]
        loop_row = steps[1]
        assert (loop_row.attempts, loop_row.exit_code, loop_row.duration_seconds, loop_row.error_message) == (
            None,
            None,
            None,
            None,
        )

    def test_skipped_step_and_loop_report_their_cause_and_a_skipped_loop_has_no_body_rows(self) -> None:
        false_skip = StepSkip(cause=SkipCause.WHEN_FALSE, expression="steps.a.exit_code == 1")
        unresolved_skip = StepSkip(cause=SkipCause.WHEN_UNRESOLVED, expression="steps.x.exit_code == 0")
        loop = ExecutionLoopNode(
            id="repair",
            max_iterations=2,
            state=NodeState.SKIPPED,
            skip=unresolved_skip,
            iterations=[ExecutionIterationRecord(number=1, steps=[ExecutionLeafNode(id="fix")])],
        )
        nodes: list[ExecutionPlanNode] = [ExecutionLeafNode(id="b", state=NodeState.SKIPPED, skip=false_skip), loop]

        steps = summarize_session_steps(_record(nodes), _REDACTOR)

        assert steps == [
            StepRunSummary(kind="step", step_id="b", state="skipped", attempts=0, skip_cause="when_false"),
            StepRunSummary(kind="loop", step_id="repair", state="skipped", iterations=0, skip_cause="when_unresolved"),
        ]

    def test_error_message_is_redacted_and_streams_are_dropped(self) -> None:
        redactor = SecretRedactor.from_environment(env={"DB_PIN": "1234"}, credential_envs=("DB_PIN",))
        leaf = ExecutionLeafNode(
            id="deploy",
            state=NodeState.FAILED,
            attempts=[_attempt(1, exit_code=1, duration_seconds=1.0, error_message="login with pin 1234 failed")],
        )

        steps = summarize_session_steps(_record([leaf]), redactor)

        assert steps[0].error_message == "login with pin [REDACTED:DB_PIN] failed"
        dumped = "".join(step.model_dump_json() for step in steps)
        assert all(text not in dumped for text in ("STDOUT-TEXT", "STDERR-TEXT", "OUTPUT-TEXT", "1234"))

    @pytest.mark.parametrize(
        ("state_json", "code", "message"),
        [
            pytest.param(None, ErrorCode.SESSION_STATE_MISSING, NO_STEP_DATA_WARNING, id="no-state"),
            pytest.param(
                "{not json",
                ErrorCode.SESSION_STATE_CORRUPT,
                "Step data could not be read: Invalid JSON: key must be a string at line 1 column 2",
                id="corrupt",
            ),
        ],
    )
    def test_unreadable_state_raises_engine_error(self, state_json: str | None, code: ErrorCode, message: str) -> None:
        """Raise EngineError with that error_code and exact message."""
        record = SessionRecord(
            project_id="proj-a",
            session_id="s",
            blueprint_name="bp",
            blueprint_key="bp",
            execution_state_json=state_json,
        )

        with pytest.raises(EngineError) as raised:
            summarize_session_steps(record, _REDACTOR)

        assert raised.value.error_code is code
        assert raised.value.message == message

    def test_summarizing_writes_nothing(self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository) -> None:
        row = seed_failed_session(
            engine_paths,
            sessions_repo,
            session_id="failed-session",
            steps=[{"id": "format", "run": "true"}, {"id": "tests", "run": "false"}],
            failed_step_id="tests",
        )
        session_dir = engine_paths.session_dir("failed-session")

        def snapshot() -> tuple[dict[str, bytes], SessionRecord | None]:
            files = {str(path): path.read_bytes() for path in session_dir.rglob("*") if path.is_file()}
            return files, sessions_repo.get("failed-session")

        before = snapshot()
        steps = summarize_session_steps(row, _REDACTOR)

        assert before[0]
        assert [(step.step_id, step.state, step.error_message) for step in steps] == [
            ("format", "completed", None),
            ("tests", "failed", SEEDED_FAILURE),
        ]
        assert snapshot() == before


class SummarizeSessionStepsRejectedPatchTests:
    def test_last_attempt_path_is_reported_and_older_state_without_it_reports_none(self) -> None:
        rejected = _attempt(2, exit_code=203, duration_seconds=1.0, error_message="gate said no")
        assert rejected.result is not None
        rejected.result.rejected_patch_path = "P"
        saved = ExecutionLeafNode(
            id="saved",
            state=NodeState.FAILED,
            attempts=[_attempt(1, exit_code=203, duration_seconds=1.0), rejected],
        )
        legacy = ExecutionLeafNode(
            id="legacy", state=NodeState.FAILED, attempts=[_attempt(1, exit_code=1, duration_seconds=1.0)]
        )

        steps = summarize_session_steps(_record([saved, legacy]), _REDACTOR)

        assert [(row.step_id, row.rejected_patch_path) for row in steps] == [("saved", "P"), ("legacy", None)]
